"""
Shikoku Electric Power Transmission & Distribution (Yonden) hourly
demand parser.

Unlike Chugoku, Shikoku publishes by CALENDAR year, one file per year:
    https://www.yonden.co.jp/nw/denkiyoho/csv/juyo_shikoku_{year}.csv
Archive confirmed available back to 2016 (via the download page itself).

File format: Shift-JIS, one metadata line, blank line, then a
`DATE,TIME,実績(万kW),供給力想定値(万kW)` header (we only need the
first 3 columns; the 4th is a supply-capacity estimate, not demand).
Date format is zero-padded (`2025/01/01`), unlike TEPCO's `2025/1/1`,
but both parse fine under the same `%Y/%m/%d` format. Unit is 万kW
(multiply by 10 for MW), same convention as TEPCO/Chugoku.

The download page (https://www.yonden.co.jp/nw/denkiyoho/download.html)
uses plain <a href> links (not JS popups like Chugoku's), so real
scraping works here -- discover_links() is a genuine safety net, not
just a documented no-op.

Any hour not actually present in a downloaded file (most relevantly:
future months of the current in-progress year) is left blank rather
than forward-filled, same policy as chugoku.py and for the same reason
-- forward-fill would silently repeat a stale value across a real gap.
"""
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

import common

REGION = "shikoku"
MIN_YEAR = 2016  # confirmed: earliest year listed on the download page

DOWNLOAD_PAGE = "https://www.yonden.co.jp/nw/denkiyoho/download.html"
BASE = "https://www.yonden.co.jp/nw/denkiyoho/csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

MISSING_VALUE = None  # None -> blank cell in CSV / null in JSON; set 0.0 if preferred


# --------------------------------------------------------------------------
# Scraping / discovery step
# --------------------------------------------------------------------------

def discover_links() -> dict:
    """Scrape the download page for real per-year CSV links.
    Returns {year: url}."""
    links = {}
    r = requests.get(DOWNLOAD_PAGE, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "juyo_shikoku_" in href and href.endswith(".csv"):
            full = requests.compat.urljoin(DOWNLOAD_PAGE, href)
            try:
                year = int(href.rsplit("_", 1)[-1].replace(".csv", ""))
                links[year] = full
            except ValueError:
                continue
    return links


# --------------------------------------------------------------------------
# Downloader
# --------------------------------------------------------------------------

def download_annual_csv(year: int, links: dict | None = None) -> bytes | None:
    url = (links or {}).get(year) or f"{BASE}/juyo_shikoku_{year}.csv"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.content
    except requests.exceptions.RequestException as e:
        print(f"  [{REGION}] {year} file unavailable ({e})")
        return None


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def parse_annual_csv(raw_bytes: bytes) -> list[tuple[str, float]]:
    text = raw_bytes.decode("shift_jis")
    lines = text.splitlines()

    header_idx = None
    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find DATE,TIME header in Shikoku annual CSV")

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


def _to_datetime(date_str: str, time_str: str) -> datetime:
    d = datetime.strptime(date_str, "%Y/%m/%d")
    h, m = time_str.split(":")
    return d + timedelta(hours=int(h), minutes=int(m))


def _mankw_to_mw(val: str):
    val = val.strip()
    if val in ("", "-", "*"):
        return None
    return float(val) * 10.0


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def build_year(year: int, links: dict | None = None) -> list[tuple[str, float]]:
    exp_hours = common.expected_hours(year)

    raw = download_annual_csv(year, links)
    source_lookup = {}
    if raw is not None:
        for ts, mw in parse_annual_csv(raw):
            source_lookup[ts] = mw

    return [(ts, source_lookup.get(ts, MISSING_VALUE)) for ts in exp_hours]


def build_and_export(year: int, out_dir: str = "data/processed", links: dict | None = None) -> None:
    if year < MIN_YEAR:
        raise ValueError(f"{REGION}: no data available before {MIN_YEAR}")

    print(f"[{REGION}] building year {year}...")
    rows = build_year(year, links)

    exp_hours = common.expected_hours(year)
    if [r[0] for r in rows] != exp_hours:
        raise ValueError(f"{REGION} {year}: hour sequence mismatch (internal bug, not a data issue)")

    n_missing = sum(1 for _, v in rows if v is None)
    if n_missing:
        print(f"  [{REGION}] {n_missing}/{len(rows)} hour(s) have no source data "
              f"-- left as {'blank' if MISSING_VALUE is None else MISSING_VALUE}")
    else:
        print(f"  [{REGION}] {year}: {len(rows)} rows, fully populated")

    common.export_csv(rows, f"{out_dir}/{REGION}/{year}.csv")
    common.export_json(rows, f"{out_dir}/{REGION}/{year}.json", REGION, year)
    print(f"[{REGION}] done: {out_dir}/{REGION}/{year}.csv (+ .json)")
