"""
Chugoku Electric Power Network (Energia) hourly demand parser.

Chugoku publishes archives by JAPANESE FISCAL YEAR (April-March), one
file per fiscal year:
    https://www.energia.co.jp/nw/jukyuu/sys/juyo-{fiscal_year}.csv
    -> covers {fiscal_year}/04/01 through {fiscal_year+1}/03/31

Deliberately simple, no cross-file stitching: for calendar year `year`,
we fetch only the `year` fiscal-year file (its Apr-Dec portion covers
Apr-Dec of `year`). Jan-Mar of `year` belongs to the PREVIOUS fiscal
year's file and is intentionally left blank rather than fetched and
stitched in -- keeps the output honest about what's actually sourced
from that year's own file. Any hour not present in the downloaded file
(e.g. months that haven't happened yet, for the current in-progress
fiscal year) is also left blank rather than forward-filled -- forward
fill would silently repeat a stale value across real gaps, which is
worse than an honest blank.

File format: Shift-JIS, one metadata line, blank line, `DATE,TIME,...`
header, then hourly rows. Unit is 万kW (multiply by 10 for MW).
"""
from datetime import datetime, timedelta

import requests

import common

REGION = "chugoku"
MIN_YEAR = 2017  # adjust down if you confirm earlier fiscal-year files exist

BASE = "https://www.energia.co.jp/nw/jukyuu/sys"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# What to write for hours we have no data for. None -> blank cell in CSV,
# null in JSON (via common.export_csv/export_json). Set to 0.0 instead if
# you'd rather see literal zeros.
MISSING_VALUE = None


# --------------------------------------------------------------------------
# Downloader
# --------------------------------------------------------------------------
# No discover_links(): Chugoku's download page renders file links via
# JavaScript popups (javascript:void(0)), not plain <a href> tags, so
# static-HTML scraping can't recover the real URL. The direct URL pattern
# below is corroborated by several independent public sources.

def download_fiscal_csv(fiscal_year: int) -> bytes | None:
    """Returns None (rather than raising) if the file isn't available --
    e.g. requesting a fiscal year that hasn't started publishing yet, or
    is too old to still be archived."""
    url = f"{BASE}/juyo-{fiscal_year}.csv"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.content
    except requests.exceptions.RequestException as e:
        print(f"  [{REGION}] fiscal year {fiscal_year} file unavailable ({e})")
        return None


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def parse_annual_csv(raw_bytes: bytes) -> list[tuple[str, float]]:
    """One fiscal-year file -> list of (datetime_str, demand_mw)."""
    text = raw_bytes.decode("shift_jis")
    lines = text.splitlines()

    header_idx = None
    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find DATE,TIME header in Chugoku annual CSV")

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

def build_year(year: int, links=None) -> list[tuple[str, float]]:
    """Return (datetime_str, demand_mw_or_None) for every hour of `year`.
    Jan-Mar: always MISSING_VALUE (not fetched, belongs to prior fiscal year).
    Apr-Dec: real value if present in the `year` fiscal-year file, else
    MISSING_VALUE (covers both a fully-missing file and an in-progress
    fiscal year that hasn't reached that date yet)."""
    exp_hours = common.expected_hours(year)

    raw = download_fiscal_csv(year)
    source_lookup = {}
    if raw is not None:
        for ts, mw in parse_annual_csv(raw):
            source_lookup[ts] = mw

    rows = []
    for ts in exp_hours:
        if ts < f"{year}-04-01":
            rows.append((ts, MISSING_VALUE))  # Jan-Mar: not sourced by design
        else:
            rows.append((ts, source_lookup.get(ts, MISSING_VALUE)))
    return rows


def build_and_export(year: int, out_dir: str = "data/processed", links=None) -> None:
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
              f"-- left as {'blank' if MISSING_VALUE is None else MISSING_VALUE} "
              f"(Jan-Mar is always blank by design; additional gaps mean the "
              f"fiscal-year file doesn't cover that date yet)")
    else:
        print(f"  [{REGION}] {year}: {len(rows)} rows, fully populated")

    common.export_csv(rows, f"{out_dir}/{REGION}/{year}.csv")
    common.export_json(rows, f"{out_dir}/{REGION}/{year}.json", REGION, year)
    print(f"[{REGION}] done: {out_dir}/{REGION}/{year}.csv (+ .json)")
