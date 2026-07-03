# -*- coding: utf-8 -*-
"""
HEPCO (Hokkaido Electric Power Network) hourly demand parser.
"""
import io
import os
import zipfile
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

import common

REGION = "hepco"
MIN_YEAR = 2016

DOWNLOAD_PAGE = "https://denkiyoho.hepco.co.jp/area_download.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-data-pipeline/1.0)"}


def discover_links() -> list:
    links = []
    r = requests.get(DOWNLOAD_PAGE, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = "shift_jis"
    soup = BeautifulSoup(r.text, "html.parser")
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip"):
            full_url = requests.compat.urljoin(DOWNLOAD_PAGE, href)
            if full_url not in links:
                links.append(full_url)
    return links


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
            if "(kW)" in l and "\u4e07kW" not in l:
                unit_multiplier = 0.001
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


def fetch_and_parse_all_zips(links: list) -> dict:
    data_dict = {}
    for url in links:
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
                for name in names:
                    with zf.open(name) as f:
                        parse_daily_csv(f.read(), data_dict)
        except Exception as e:
            print(f"  [warn] Failed to process {url}: {e}")
    return data_dict


def _to_datetime(date_str: str, time_str: str):
    try:
        date_str = date_str.strip('"')
        time_str = time_str.strip('"')
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
    if links is None:
        links = discover_links()
        
    print(f"  [{REGION}] Fetching all seasonal ZIPs to isolate year {year}...")
    data_dict = fetch_and_parse_all_zips(links)
    
    year_prefix = f"{year}-"
    rows = [(dt, mw) for dt, mw in data_dict.items() if dt.startswith(year_prefix)]
    rows.sort(key=lambda r: r[0])
    return rows


def build_and_export(year: int, out_dir: str = "data/processed", links: list = None) -> None:
    if year < MIN_YEAR:
        raise ValueError(f"{REGION}: no full-year data available before {MIN_YEAR}")
        
    print(f"[{REGION}] building year {year}...")
    
    if links is None:
        links = discover_links()
        
    rows = build_year(year, links)
    
    if not rows:
        raise ValueError(f"{REGION}: No rows parsed for year {year}. Check ZIP structure.")
        
    rows = common.fill_gaps(rows, year)
    common.validate(rows, year, REGION)
    
    # Explicitly create directory and resolve absolute paths
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
    all_links = discover_links()
    # Test for 2023
    build_and_export(2023, links=all_links)