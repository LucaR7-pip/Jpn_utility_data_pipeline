# -*- coding: utf-8 -*-
"""
Kansai Transmission and Distribution hourly demand parser.
"""
import io
import os
import zipfile
from datetime import datetime, timedelta

import requests

import common

REGION = "kansai"
MIN_YEAR = 2016

# Hardcoded URL pattern to avoid scraping issues
# Pattern: https://www.kansai-td.co.jp/yamasou/YYYYMM_jisseki.zip
BASE_URL = "https://www.kansai-td.co.jp/yamasou/{year}{month:02d}_jisseki.zip"
# Use a standard browser User-Agent to avoid being blocked by the server
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def discover_links(year: int) -> list:
    """Generate the 12 monthly ZIP URLs for the given year."""
    return [BASE_URL.format(year=year, month=m) for m in range(1, 13)]


def parse_daily_csv(raw_bytes: bytes, data_dict: dict) -> None:
    """Parse one day's CSV and append to the data_dict."""
    try:
        text = raw_bytes.decode("shift_jis")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="ignore")

    lines = text.splitlines()
    header_idx = None
    unit_multiplier = 10.0  # man-kW -> MW

    # Find the exact header row for the hourly table
    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,"):
            header_idx = i
            break

    if header_idx is None:
        return  # Skip if file doesn't contain the expected hourly table

    for l in lines[header_idx + 1:]:
        l = l.strip()
        if not l:
            break  # Blank line marks the end of the 24-hour block
            
        parts = l.split(",")
        if len(parts) < 3:
            continue
            
        date_str, time_str, val_str = parts[0], parts[1], parts[2]
        
        dt = _to_datetime(date_str, time_str)
        mw = _val_to_mw(val_str, unit_multiplier)
        
        if dt and mw is not None:
            dt_str = dt.strftime("%Y-%m-%d %H:%M")
            data_dict[dt_str] = mw


def fetch_and_parse_year(year: int) -> dict:
    """Download all 12 monthly ZIPs for the year, parse daily CSVs."""
    data_dict = {}
    urls = discover_links(year)
    
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            
            # Skip 404s gracefully (e.g., if December data isn't published yet)
            if r.status_code == 404:
                continue
                
            r.raise_for_status()
            
            # Prevent parsing HTML error pages as ZIP
            if "html" in r.headers.get("Content-Type", "").lower():
                print(f"  [warn] Skipping {url}: returned HTML, not ZIP.")
                continue
                
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
                for name in names:
                    with zf.open(name) as f:
                        parse_daily_csv(f.read(), data_dict)
        except Exception as e:
            print(f"  [warn] Failed to process {url}: {e}")
            
    return data_dict


def _to_datetime(date_str: str, time_str: str):
    date_str = date_str.strip(' "\'')
    time_str = time_str.strip(' "\'')
    date_str = date_str.replace('-', '/')
    
    try:
        d = datetime.strptime(date_str, "%Y/%m/%d")
        h, m = time_str.split(":")
        return d + timedelta(hours=int(h), minutes=int(m))
    except (ValueError, IndexError):
        return None

def _val_to_mw(val: str, multiplier: float):
    val = val.strip().replace(",", "")
    if val in ("", "-", "*", "NaN", "null"):
        return None
    try:
        return float(val) * multiplier
    except ValueError:
        return None


def build_year(year: int, links: list = None) -> list:
    """Return sorted (datetime_str, demand_mw) covering Jan 1 00:00
    through Dec 31 23:00 of `year`."""
    print(f"  [{REGION}] Fetching all monthly ZIPs for year {year}...")
    data_dict = fetch_and_parse_year(year)
    
    # Filter strictly for the target calendar year
    year_prefix = f"{year}-"
    rows = [(dt, mw) for dt, mw in data_dict.items() if dt.startswith(year_prefix)]
    rows.sort(key=lambda r: r[0])
    return rows


def build_and_export(year: int, out_dir: str = "data/processed", links: list = None) -> None:
    if year < MIN_YEAR:
        raise ValueError(f"{REGION}: no full-year data available before {MIN_YEAR}")
        
    print(f"[{REGION}] building year {year}...")
    
    rows = build_year(year, links)
    
    if not rows:
        raise ValueError(f"{REGION}: No rows parsed for year {year}. Check CSV structure.")
        
    rows = common.fill_gaps(rows, year)
    common.validate(rows, year, REGION)
    
    target_dir = os.path.join(out_dir, REGION)
    os.makedirs(target_dir, exist_ok=True)
    
    csv_path = os.path.abspath(os.path.join(target_dir, f"{year}.csv"))
    json_path = os.path.abspath(os.path.join(target_dir, f"{year}.json"))
    
    common.export_csv(rows, csv_path)
    common.export_json(rows, json_path, REGION, year)
    
    print(f"[{REGION}] SUCCESS! Saved files to:")
    print(f"  -> {csv_path}")
    print(f"  -> {json_path}")

# --------------------------------------------------------------------------
# Local Testing
# --------------------------------------------------------------------------
if __name__ == "__main__":
    build_and_export(2023)