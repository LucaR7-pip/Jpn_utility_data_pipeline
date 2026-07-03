# Japan Utility Hourly Demand Pipeline

Monorepo automating: **scrape → download → process → convert**, looped
over any set of years and any of the 10 Japanese utility companies.

One repo, one shared toolkit (`scripts/common.py`), one GitHub Actions
workflow -- each company gets its own parser module following the same
interface, so adding company #2 never requires touching `run.py` or the
workflow logic, just a new `scripts/{company}.py` file.

## Companies

| Status | Module | Company |
|---|---|---|
| ✅ done | `scripts/tepco.py` | TEPCO (Tokyo) -- full data from 2017 (2016 partial, excluded) |
| ⏳ next | `scripts/hokkaido.py` | Hokkaido Electric |
| ⏳ | `scripts/tohoku.py` | Tohoku Electric |
| ⏳ | `scripts/chubu.py` | Chubu Electric |
| ⏳ | `scripts/hokuriku.py` | Hokuriku Electric |
| ⏳ | `scripts/kansai.py` | Kansai Electric |
| ⏳ | `scripts/chugoku.py` | Chugoku Electric |
| ⏳ | `scripts/shikoku.py` | Shikoku Electric |
| ⏳ | `scripts/kyushu.py` | Kyushu Electric |
| ⏳ | `scripts/okinawa.py` | Okinawa Electric |

## Folder structure

```
pipeline/
├── run.py                     # generic CLI, dispatches to any scripts/{company}.py
├── requirements.txt
├── scripts/
│   ├── common.py               # shared gap-fill / validate / export (used by all companies)
│   ├── tepco.py                # TEPCO-specific scrape/download/parse logic
│   └── ...                     # one file per company, same interface as tepco.py
└── data/
    ├── raw/{company}/           # (optional) raw downloaded files, not auto-populated by default
    └── processed/{company}/
        ├── {year}.csv            # Datetime,Demand_MW  (8760 or 8784 rows + header)
        └── {year}.json           # {"region","year","unit":"MW","data":[[dt,mw],...]}
```

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Explicit years, one company
python run.py --company tepco --years 2019 2020 2021

# Range
python run.py --company tepco --start 2017 --end 2025

# Skip the scrape/discovery step and construct URLs directly
# (faster, but won't catch upstream naming/URL changes)
python run.py --company tepco --start 2017 --end 2025 --skip-scrape

# Custom output location
python run.py --company tepco --years 2024 --out-dir /path/to/data/processed
```

## Adding a new company

1. Create `scripts/{company}.py` implementing:
   - `REGION = "..."` (module-level constant, used for output paths)
   - `MIN_YEAR = ...` (optional, if early years are partial/unavailable)
   - `discover_links()` (optional "scrape" stage -- return a dict your
     download functions can use)
   - `build_and_export(year, out_dir="data/processed", links=None)`
     following the same shape as `tepco.py`'s version (fetch -> parse ->
     `common.fill_gaps()` -> `common.validate()` -> `common.export_csv()`
     + `common.export_json()`)
2. That's it -- `run.py` picks up any `scripts/{company}.py` automatically
   via `--company {company}`.

## Annual update routine (manual)

GitHub Actions' cloud-hosted runners get blocked (403 Forbidden) by
TEPCO and likely the other utility sites -- they reject traffic from
foreign datacenter IP ranges. Rather than fight that with a self-hosted
runner, this repo is used as **code + data storage only**; the yearly
update is a short manual routine:

1. Pull the latest repo (or re-download it) onto your machine.
2. Install dependencies once: `python -m pip install -r requirements.txt`
3. Run the pipeline for the new year, e.g.:
   ```
   python run.py --company tepco --years 2026
   ```
4. Commit and push the new `data/processed/tepco/2026.csv` (+ `.json`)
   via GitHub Desktop (or `git add`/`commit`/`push`).

Repeat step 3-4 for each company once its parser exists. Takes a few
minutes once a year -- simple, reliable, no infrastructure to babysit.

## Referencing the data from other tools

Once pushed, files are fetchable directly by URL (works for public
repos, no auth needed) -- useful for pulling straight into Grasshopper,
a web visualizer, or any other script:

```
https://raw.githubusercontent.com/{your-username}/{repo}/main/data/processed/tepco/2021.csv
```

## The 4 stages

1. **Scrape** — `tepco.discover_links()` reads TEPCO's two download pages
   (`download-j.html`, `download_year-j.html`) and collects every annual-CSV
   and monthly-ZIP link actually published. This is a safety net: if TEPCO
   renames a file, we find out via a missing link rather than a silent
   wrong-URL 404 deep in a loop. Falls back to direct URL construction if
   scraping fails or is skipped.

2. **Download** — fetches the annual CSV (2016–2022/03) or the monthly ZIPs
   (2022/04–present) via `requests`.

3. **Process** — parses each source format:
   - Annual CSV: already hourly, straightforward parse.
   - Monthly ZIP: contains **one CSV per day** (not one CSV per month —
     this was confirmed from real sample files). Each daily file has a
     metadata block, an hourly table, and a 5-minute table; only the
     hourly table is extracted.
   - Converts 万kW → MW (×10) throughout.
   - The 2022 calendar year is a hybrid: Jan–Mar from the annual CSV,
     Apr–Dec from monthly ZIPs, since TEPCO's format break happened
     2022/04/01.

4. **Convert** — gap-fills any missing hours (forward-fill), validates the
   row count is exactly 8760 (or 8784 for leap years) and contiguous, then
   exports both CSV and JSON in the standard cross-company format.

## Known data quirks (TEPCO-specific)

- Encoding is **Shift-JIS**, not UTF-8.
- Unit is **万kW** (10,000 kW), not kW or MW directly.
- A demand value of `0` for *today's date only* means "not yet finalized" —
  not applicable to historical/past-year data, but worth remembering if this
  script is ever pointed at the current in-progress year.
- Data before and after 2022/04/01 uses a different area-total methodology
  per TEPCO's own notice — not directly comparable across the boundary.

## Notes on testing

Network access to `tepco.co.jp` was unavailable in the sandbox this was
built in, so the download functions are validated by construction (same
URL patterns and parsing logic tested against real sample files you
provided) rather than a live end-to-end run. Recommend running once
locally or via GitHub Actions to confirm live connectivity before relying
on it for the scheduled annual job.
