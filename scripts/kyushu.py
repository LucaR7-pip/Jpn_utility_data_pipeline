"""
Kyushu Electric Power Transmission & Distribution hourly demand parser.

Kyushu publishes ONE FILE PER DAY, every day, with no monthly/annual
bundling (no ZIP, unlike TEPCO's post-2022 format):
    https://www.kyuden.co.jp/td_power_usages/csv/juyo-hourly-{YYYYMMDD}.csv

This means building one calendar year here means ~365 individual HTTP
requests (366 in a leap year), not 12 or 1 like the other companies.
Expect this to be noticeably slower to run.

Each daily file has the same shape as TEPCO's daily files: a metadata/
peak-forecast block, an HOURLY table (24 rows -- what we want), then a
5-MINUTE table (which we ignore). Header line for the hourly table is
`DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)`. Shift-JIS
encoding, unit is 万kW (multiply by 10 for MW), same convention as the
other companies.

Any day whose file 404s (holiday gaps, dates outside the archive, or a
transient failure) is left blank for all 24 of its hours rather than
raising or forward-filling -- consistent with chugoku.py/shikoku.py's
policy: an honest gap beats a silently-repeated stale value.
"""
from datetime import date, datetime, timedelta
import time
import requests

import common

REGION = "kyushu"
MIN_YEAR = 2016  # adjust down if you confirm earlier daily archives exist

BASE = "https://www.kyuden.co.jp/td_power_usages/csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

MISSING_VALUE = None  # None -> blank cell in CSV / null in JSON; set 0.0 if preferred


# --------------------------------------------------------------------------
# Downloader
# --------------------------------------------------------------------------

def download_daily_csv(day: date) -> bytes | None:
    url = f"{BASE}/juyo-hourly-{day:%Y%m%d}.csv"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.content
    except requests.exceptions.RequestException:
        return None


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def parse_daily_csv(raw_bytes: bytes) -> list[tuple[str, float]]:
    """Extract just the 24-row hourly block from one daily file."""
    # errors="replace" prevents fatal crashes from stray un-decodable characters
    text = raw_bytes.decode("shift_jis", errors="replace")
    lines = text.splitlines()

    header_idx = None
    for i, l in enumerate(lines):
        # Robust substring check ignores invisible BOMs or slight header column variations
        if "DATE" in l and "TIME" in l and "当日実績" in l:
            header_idx = i
            break
            
    if header_idx is None:
        # Failing gracefully allows the single day to be marked missing rather than crashing the year
        return []

    rows = []
    for l in lines[header_idx + 1:]:
        l = l.strip()
        
        # Stop safely if we hit a blank line OR the 5-minute table header
        if not l or l.startswith("DATE") or "当日実績" in l:
            break
            
        parts = l.split(",")
        if len(parts) < 3:
            continue
            
        date_str, time_str, val_mankw = parts[0], parts[1], parts[2]
        
        try:
            dt = _to_datetime(date_str, time_str)
            mw = _mankw_to_mw(val_mankw)
            rows.append((dt.strftime("%Y-%m-%d %H:%M"), mw))
        except ValueError:
            # Skip rows with entirely unparseable data rather than crashing
            continue
        
        # Hourly block is strictly exactly 24 rows. Once reached, break out.
        if len(rows) == 24:
            break
            
    return rows


def _to_datetime(date_str: str, time_str: str) -> datetime:
    d = datetime.strptime(date_str, "%Y/%m/%d")
    h, m = time_str.split(":")
    return d + timedelta(hours=int(h), minutes=int(m))


def _mankw_to_mw(val: str):
    val = val.strip()
    if val in ("", "-", "*"):
        return None
    try:
        # Prevent numbers containing commas like "1,234" from crashing the float cast
        return float(val.replace(',', '')) * 10.0
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def build_year(year: int, links=None) -> list[tuple[str, float]]:
    exp_hours = common.expected_hours(year)
    source_lookup = {}

    day = date(year, 1, 1)
    end = date(year, 12, 31)
    
    # Optimization: Do not attempt to aggressively fetch dates in the future.
    # Essential for avoiding infinite 404 flooding when run dynamically on future years.
    today = date.today()
    if year == today.year:
        end = today
    elif year > today.year:
        print(f"  [{REGION}] {year} is in the future. Returning blank template.")
        return [(ts, MISSING_VALUE) for ts in exp_hours]

    failed_days = []
    while day <= end:
        raw = download_daily_csv(day)
        if raw is None:
            failed_days.append(day.isoformat())
        else:
            for ts, mw in parse_daily_csv(raw):
                source_lookup[ts] = mw
        
        day += timedelta(days=1)
        time.sleep(0.02) # Very slight delay to prevent aggressive server rate-limiting

    if failed_days:
        print(f"  [{REGION}] {len(failed_days)} day(s) unavailable, left blank "
              f"(e.g. {failed_days[:3]}{' ...' if len(failed_days) > 3 else ''})")

    return [(ts, source_lookup.get(ts, MISSING_VALUE)) for ts in exp_hours]


def build_and_export(year: int, out_dir: str = "data/processed", links=None) -> None:
    if year < MIN_YEAR:
        raise ValueError(f"{REGION}: no data available before {MIN_YEAR}")

    print(f"[{REGION}] building year {year}... (this fetches ~365 individual daily files, be patient)")
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