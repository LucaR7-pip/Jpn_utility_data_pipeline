# -*- coding: utf-8 -*-
"""
Hokuriku Electric Power Transmission hourly demand parser.
"""
import os
from datetime import datetime, timedelta

import requests

import common

REGION = "hokuriku"
MIN_YEAR = 2016

# Hardcoded URL pattern to avoid scraping issues
# Pattern: https://www.rikuden.co.jp/nw/denki-yoho/csv/juyo_05_YYYYMMDD.csv
BASE_URL = "https://www.rikuden.co.jp/nw/denki-yoho/csv/juyo_05_{year}{month:02d}{day:02d}.csv"
# Use a standard browser User-Agent to avoid being blocked
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def download_daily_csv(year: int, month: int, day: int) -> bytes:
    url = BASE_URL.format(year=year, month=month, day=day)
    r = requests.get(url, headers=HEADERS, timeout=60)
    
    # Skip 404s gracefully (e.g., future dates or missing days)
    if r.status_code == 404:
        return None
        
    r.raise_for_status()
    
    # Prevent parsing HTML error pages as CSV
    if "html" in r.headers.get("Content-Type", "").lower():
        return None
        
    return r.content


def parse_daily_csv(raw_bytes: bytes, data_dict: dict) -> None:
    try:
        text = raw_bytes.decode("shift_jis")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="ignore")

    lines = text.splitlines()
    header_idx = None
    unit_multiplier = 10.0  # man-kW -> MW

    for i, l in enumerate(lines):
        if l.startswith("DATE,TIME,"):
            header_idx = i
            break

    if header_idx is None:
        return

    for l in lines[header_idx + 1:]:
        l = l.strip()
        if not l:
            break
            
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
    data_dict = {}
    
    # Iterate through all days of the year
    current_date = datetime(year, 1, 1)
    while current_date.year == year:
        try:
            raw = download_daily_csv(current_date.year, current_date.month, current_date.day)
            if raw:
                parse_daily_csv(raw, data_dict)
        except Exception as e:
            # Ignore errors for individual days and continue
            pass
            
        current_date += timedelta(days=1)
        
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
    print(f"  [{REGION}] Fetching all daily CSVs for year {year}...")
    data_dict = fetch_and_parse_year(year)
    
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