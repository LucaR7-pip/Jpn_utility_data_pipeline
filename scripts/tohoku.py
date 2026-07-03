# -*- coding: utf-8 -*-
"""
Tohoku Electric Power Network hourly demand parser.
"""
import os
from datetime import datetime, timedelta

import requests

import common

REGION = "tohoku"
MIN_YEAR = 2016

# Hardcoded URL pattern to avoid scraping issues
BASE_URL = "https://setsuden.nw.tohoku-epco.co.jp/common/demand/juyo_{year}_tohoku.csv"
# Use a standard browser User-Agent to avoid being blocked by the server
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def discover_links() -> dict:
    # Since the URL pattern is predictable, we can just return an empty dict.
    # The download function will construct the URL directly.
    return {}


def download_annual_csv(year: int, links: dict = None) -> bytes:
    # Ignore the links dict, just construct the URL directly
    url = BASE_URL.format(year=year)
    
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    
    # Strict check to prevent parsing HTML error pages as CSV
    content_type = r.headers.get("Content-Type", "").lower()
    if "html" in content_type or "<html" in r.text[:100].lower():
        raise ValueError(f"{REGION}: URL for {year} returned HTML, not CSV. URL: {url}")
        
    return r.content


def parse_annual_csv(raw_bytes: bytes, target_year: int) -> list:
    try:
        text = raw_bytes.decode("shift_jis")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="ignore")

    rows = []
    unit_multiplier = 10.0  # Default to man-kW -> MW (multiply by 10)

    for l in text.splitlines():
        l = l.strip()
        if not l:
            continue
        
        # Auto-detect delimiter (comma, semicolon, tab)
        if ',' in l:
            parts = l.split(',')
        elif ';' in l:
            parts = l.split(';')
        elif '\t' in l:
            parts = l.split('\t')
        else:
            continue
            
        if len(parts) < 3:
            continue
            
        date_str = parts[0].strip(' "\'')
        time_str = parts[1].strip(' "\'')
        val_str = parts[2].strip(' "\'')
        
        dt = _to_datetime(date_str, time_str)
        
        # If the first two columns successfully parse as a date, it's a data row.
        if dt and dt.year == target_year:
            mw = _val_to_mw(val_str, unit_multiplier)
            if mw is not None:
                rows.append((dt.strftime("%Y-%m-%d %H:%M"), mw))
                
    return rows


def _to_datetime(date_str: str, time_str: str):
    date_str = date_str.strip(' "\'')
    time_str = time_str.strip(' "\'')
    date_str = date_str.replace('-', '/') # Handle YYYY-MM-DD
    
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


def build_year(year: int, links: dict = None) -> list:
    raw = download_annual_csv(year, links)
    rows = parse_annual_csv(raw, year)
    rows.sort(key=lambda r: r[0])
    return rows


def build_and_export(year: int, out_dir: str = "data/processed", links: dict = None) -> None:
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
    build_and_export(2017)