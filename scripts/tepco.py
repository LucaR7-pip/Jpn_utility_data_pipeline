"""
TEPCO (Tokyo Electric Power Grid) hourly demand parser.

Two eras, different download mechanics:
  - 2016 .. 2022/03  : one annual CSV, already hourly, 8760/8784 rows
        https://www.tepco.co.jp/forecast/html/images/juyo-{year}.csv
  - 2022/04 .. present: one ZIP per month, containing one CSV per day.
    Each daily CSV has TWO stacked tables (hourly block + 5-min block);
    we only want the hourly block.
        https://www.tepco.co.jp/forecast/html/images/{yyyymm}_power_usage.zip

Output: list of (datetime_str "YYYY-MM-DD HH:MM", demand_mw: float)
"""
import io
import re
import zipfile
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

import common

REGION = "tepco"
ERA_BREAK = datetime(2022, 4, 1)  # first day handled by the new (zip) format
MIN_YEAR = 2017  # 2016 data starts partway through April; not a full calendar year

BASE = "https://www.tepco.co.jp/forecast/html/images"
DOWNLOAD_PAGE = "https://www.tepco.co.jp/forecast/html/download-j.html"
DOWNLOAD_YEAR_PAGE = "https://www.tepco.co.jp/forecast/html/download_year-j.html"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-data-pipeline/1.0)"}


# --------------------------------------------------------------------------
# Scraping / discovery step
# --------------------------------------------------------------------------
# TEPCO URLs follow a fixed, predictable pattern, so in principle we could
# just construct them directly. But relying on that blindly means a silent
# naming change breaks the pipeline with a confusing 404 deep inside a
# loop. Instead we scrape the actual download page(s) first and only
# proceed with links that really exist there -- this is the "scraping"
# phase proper, distinct from "downloading".

def discover_links() -> dict:
    """Scrape both TEPCO download pages and return every annual-CSV and
    monthly-ZIP link actually published, keyed for quick lookup:
        {"annual": {year: url}, "monthly": {(year, month): url}}
    """
    annual = {}
    monthly = {}

    for page_url in (DOWNLOAD_PAGE, DOWNLOAD_YEAR_PAGE):
        r = requests.get(page_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = requests.compat.urljoin(page_url, href)

            m = re.search(r"juyo-(\d{4})\.csv", href)
            if m:
                annual[int(m.group(1))] = full
                continue

            m = re.search(r"(\d{4})(\d{2})_power_usage\.zip", href)
            if m:
                monthly[(int(m.group(1)), int(m.group(2)))] = full

    return {"annual": annual, "monthly": monthly}


# --------------------------------------------------------------------------
# Downloaders (require real internet access -- will work in GitHub Actions
# or on a local machine; blocked in this sandbox's restricted network)
# --------------------------------------------------------------------------

def download_annual_csv(year: int, links: dict | None = None) -> bytes:
    url = (links or {}).get("annual", {}).get(year) or f"{BASE}/juyo-{year}.csv"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content


def download_month_zip(year: int, month: int, links: dict | None = None) -> bytes:
    url = (links or {}).get("monthly", {}).get((year, month)) \
        or f"{BASE}/{year:04d}{month:02d}_power_usage.zip"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content


# --------------------------------------------------------------------------
# Parsers (pure functions, testable offline against saved sample files)
# --------------------------------------------------------------------------

def parse_annual_csv(raw_bytes: bytes) -> list[tuple[str, float]]:
    """Pre-2022/04 annual file. Already one row per hour."""
    text = raw_bytes.decode("shift_jis")
    lines = text.splitlines()

    header_idx = None
    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find DATE,TIME header in annual CSV")

    rows = []
    for l in lines[header_idx + 1:]:
        l = l.strip()
        if not l:
            continue
        parts = l.split(",")
        date_str, time_str, val_mankw = parts[0], parts[1], parts[2]
        dt = _to_datetime(date_str, time_str)
        mw = _mankw_to_mw(val_mankw)
        rows.append((dt.strftime("%Y-%m-%d %H:%M"), mw))
    return rows


def parse_daily_csv(raw_bytes: bytes) -> list[tuple[str, float]]:
    """One day's file from inside a monthly ZIP. Extract only the
    hourly block (24 rows), ignore the 5-min block and metadata."""
    text = raw_bytes.decode("shift_jis")
    lines = text.splitlines()

    header_idx = None
    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,当日実績(万kW)"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find hourly header in daily CSV")

    rows = []
    for l in lines[header_idx + 1:]:
        l = l.strip()
        if not l:
            break  # blank line = end of the hourly block
        parts = l.split(",")
        date_str, time_str, val_mankw = parts[0], parts[1], parts[2]
        dt = _to_datetime(date_str, time_str)
        mw = _mankw_to_mw(val_mankw)
        rows.append((dt.strftime("%Y-%m-%d %H:%M"), mw))
    return rows


def parse_month_zip(raw_bytes: bytes) -> list[tuple[str, float]]:
    """Unzip a monthly archive, parse every daily CSV inside, concat."""
    rows = []
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
        for name in names:
            with zf.open(name) as f:
                rows.extend(parse_daily_csv(f.read()))
    return rows


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _to_datetime(date_str: str, time_str: str) -> datetime:
    # date_str like "2021/1/1", time_str like "0:00"
    d = datetime.strptime(date_str, "%Y/%m/%d")
    h, m = time_str.split(":")
    return d + timedelta(hours=int(h), minutes=int(m))


def _mankw_to_mw(val: str) -> float:
    """TEPCO reports in 万kW (10,000 kW units). 1 万kW = 10 MW."""
    val = val.strip()
    if val in ("", "-", "*"):
        return None  # missing marker -> caller fills gap later
    return float(val) * 10.0


# --------------------------------------------------------------------------
# Orchestration: build one full calendar year, era-aware
# --------------------------------------------------------------------------

def build_year(year: int, links: dict | None = None) -> list[tuple[str, float]]:
    """Return sorted (datetime_str, demand_mw) covering Jan 1 00:00
    through Dec 31 23:00 of `year`, pulling from whichever source(s)
    apply. 2022 is a hybrid year (Jan-Mar annual CSV, Apr-Dec zips).
    `links` is the dict from discover_links(); if omitted, URLs are
    constructed directly from the known naming pattern."""
    rows: list[tuple[str, float]] = []

    year_start = datetime(year, 1, 1)
    year_end_exclusive = datetime(year + 1, 1, 1)

    if year_end_exclusive <= ERA_BREAK:
        raw = download_annual_csv(year, links)
        rows = parse_annual_csv(raw)
    elif year_start >= ERA_BREAK:
        for month in range(1, 13):
            raw = download_month_zip(year, month, links)
            rows.extend(parse_month_zip(raw))
    else:
        raw = download_annual_csv(year, links)
        annual_rows = parse_annual_csv(raw)
        rows.extend([r for r in annual_rows if r[0] < "2022-04-01"])
        for month in range(4, 13):
            raw = download_month_zip(year, month, links)
            rows.extend(parse_month_zip(raw))

    rows.sort(key=lambda r: r[0])
    return rows


# --------------------------------------------------------------------------
# Full pipeline: fetch -> parse -> gap-fill -> validate -> export
# --------------------------------------------------------------------------

def build_and_export(year: int, out_dir: str = "data/processed", links: dict | None = None) -> None:
    if year < MIN_YEAR:
        raise ValueError(f"{REGION}: no full-year data available before {MIN_YEAR} (year {year} starts partway through the calendar year)")
    print(f"[{REGION}] building year {year}...")
    rows = build_year(year, links)
    rows = common.fill_gaps(rows, year)
    common.validate(rows, year, REGION)
    common.export_csv(rows, f"{out_dir}/{REGION}/{year}.csv")
    common.export_json(rows, f"{out_dir}/{REGION}/{year}.json", REGION, year)
    print(f"[{REGION}] done: {out_dir}/{REGION}/{year}.csv (+ .json)")
