"""
Okinawa Electric Power Transmission & Distribution hourly demand parser.

Same shape as kyushu.py: ONE FILE PER DAY, every day, no monthly/annual
bundling:
    https://www.okiden.co.jp/denki2/juyo_10_{YYYYMMDD}.csv

The website organizes its browsing UI by fiscal-year folders with one
index page per month (e.g. dl/2025/202504.html), but since each day's
file is addressed directly by its own YYYYMMDD, we don't need to touch
that folder structure at all -- just loop Jan 1..Dec 31 of the target
CALENDAR year and construct each URL directly (no fiscal-year stitching
needed here, unlike chugoku.py).

Building one calendar year means ~365 individual HTTP requests, same
performance characteristics as kyushu.py -- expect it to be slow.

Each daily file has the same shape as Kyushu's: a metadata/peak-forecast
block, an HOURLY table (24 rows -- what we want, header
`DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)`), then a
5-MINUTE table (ignored). Shift-JIS encoding, unit 万kW (x10 for MW).

NOTE on 2015: Okinawa separately publishes a single bulk file for FY2015
(juyo_2015_okiden.csv) that the utility itself flags as using a
different methodology (generation-end values, not the demand-end values
used from 2016 onward) -- not comparable to later years, so this parser
does not support 2015 and MIN_YEAR starts at 2016.

Any day whose file 404s is left blank for all 24 of its hours rather
than raising or forward-filling, same policy as the other companies.
"""
from datetime import date, datetime, timedelta

import requests

import common

REGION = "okinawa"
MIN_YEAR = 2016  # 2015 uses an incompatible methodology (generation-end, not demand-end); excluded

BASE = "https://www.okiden.co.jp/denki2"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

MISSING_VALUE = None  # None -> blank cell in CSV / null in JSON; set 0.0 if preferred


# --------------------------------------------------------------------------
# Downloader
# --------------------------------------------------------------------------
# No discover_links(): like Kyushu, the URL is fixed and date-driven, so
# there's nothing to discover -- we construct each day's URL directly
# and handle 404s gracefully. (The monthly index pages do have real
# scrapeable <a href> links, unlike Chugoku's JS popups, but scraping
# 12 pages/year for marginal benefit over a confirmed direct pattern
# isn't worth the extra requests.)

def download_daily_csv(day: date) -> bytes | None:
    url = f"{BASE}/juyo_10_{day:%Y%m%d}.csv"
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
    text = raw_bytes.decode("shift_jis")
    lines = text.splitlines()

    header_idx = None
    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,当日実績(万kW)"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find hourly header in Okinawa daily CSV")

    rows = []
    for l in lines[header_idx + 1:]:
        l = l.strip()
        if not l:
            break
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
    exp_hours = common.expected_hours(year)
    source_lookup = {}

    day = date(year, 1, 1)
    end = date(year, 12, 31)
    failed_days = []
    while day <= end:
        raw = download_daily_csv(day)
        if raw is None:
            failed_days.append(day.isoformat())
        else:
            for ts, mw in parse_daily_csv(raw):
                source_lookup[ts] = mw
        day += timedelta(days=1)

    if failed_days:
        print(f"  [{REGION}] {len(failed_days)} day(s) unavailable, left blank "
              f"(e.g. {failed_days[:3]}{' ...' if len(failed_days) > 3 else ''})")

    return [(ts, source_lookup.get(ts, MISSING_VALUE)) for ts in exp_hours]


def build_and_export(year: int, out_dir: str = "data/processed", links=None) -> None:
    if year < MIN_YEAR:
        raise ValueError(f"{REGION}: no data available before {MIN_YEAR} "
                          f"(2015 uses an incompatible methodology)")

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
