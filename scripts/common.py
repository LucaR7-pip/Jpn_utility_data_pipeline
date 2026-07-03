"""
Shared utilities for all 10 utility parsers.

Standard row format everywhere: (datetime_str "YYYY-MM-DD HH:MM", demand_mw)

Standard output:
  CSV  -> Datetime,Demand_MW   (row1 header, rows 2..8761/8785 data)
  JSON -> {"region": ..., "year": ..., "unit": "MW", "data": [[dt, mw], ...]}
"""
import json
import csv
import calendar
from datetime import datetime, timedelta
from pathlib import Path


def expected_hours(year: int) -> list[str]:
    n_hours = 8784 if calendar.isleap(year) else 8760
    start = datetime(year, 1, 1)
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M") for i in range(n_hours)]


def fill_gaps(rows: list[tuple[str, float]], year: int) -> list[tuple[str, float]]:
    """Reindex to the full expected hour set for `year`.
    Missing hours or None values are forward-filled from the
    most recent known value. Raises if the very first hour of
    the year is missing (nothing to forward-fill from)."""
    lookup = dict(rows)
    exp = expected_hours(year)

    out = []
    last_valid = None
    missing = []
    for ts in exp:
        val = lookup.get(ts)
        if val is None:
            missing.append(ts)
            val = last_valid
        else:
            last_valid = val
        out.append((ts, val))

    if out[0][1] is None:
        raise ValueError(f"First hour of {year} has no data and nothing to forward-fill from.")

    if missing:
        print(f"  [warn] {len(missing)} hour(s) forward-filled: {missing[:5]}{' ...' if len(missing) > 5 else ''}")

    return out


def validate(rows: list[tuple[str, float]], year: int, region: str) -> None:
    exp_n = 8784 if calendar.isleap(year) else 8760
    if len(rows) != exp_n:
        raise ValueError(f"{region} {year}: expected {exp_n} rows, got {len(rows)}")
    exp_hours = expected_hours(year)
    got_hours = [r[0] for r in rows]
    if got_hours != exp_hours:
        raise ValueError(f"{region} {year}: hour sequence mismatch (gaps or ordering issue)")
    if any(v is None for _, v in rows):
        raise ValueError(f"{region} {year}: unresolved None values remain after gap-fill")
    print(f"  [ok] {region} {year}: {len(rows)} rows validated, contiguous, no gaps")


def export_csv(rows: list[tuple[str, float]], out_path: str) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Datetime", "Demand_MW"])
        for dt, mw in rows:
            w.writerow([dt, round(mw, 2) if mw is not None else ""])


def export_json(rows: list[tuple[str, float]], out_path: str, region: str, year: int) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "region": region,
        "year": year,
        "unit": "MW",
        "data": [[dt, round(mw, 2) if mw is not None else None] for dt, mw in rows],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
