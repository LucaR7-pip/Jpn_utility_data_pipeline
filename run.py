"""
Generic pipeline CLI for the whole utility-data-pipeline monorepo.

Every company gets its own module in scripts/{company}.py, following the
same interface (see scripts/tepco.py as the reference implementation):
    REGION: str
    MIN_YEAR: int (optional; earliest full calendar year available)
    discover_links() -> dict           (optional "scrape" stage)
    build_and_export(year, out_dir, links=None) -> None

This CLI dynamically imports whichever company module you ask for, so
adding a new company never requires touching run.py.

Usage:
    python run.py --company tepco --years 2019 2020 2021
    python run.py --company tepco --start 2017 --end 2025
    python run.py --company hokkaido --start 2017 --end 2025 --skip-scrape
"""
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))


def parse_args():
    p = argparse.ArgumentParser(description="Utility hourly-demand pipeline")
    p.add_argument("--company", required=True,
                    help="Company module name, e.g. tepco (must exist as scripts/{company}.py)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--years", nargs="+", type=int, help="Explicit list of years")
    g.add_argument("--start", type=int, help="Start year (use with --end)")
    p.add_argument("--end", type=int, help="End year, inclusive (use with --start)")
    p.add_argument("--out-dir", default="data/processed", help="Output directory (default: data/processed)")
    p.add_argument("--skip-scrape", action="store_true",
                    help="Skip the discovery/scrape step and construct URLs directly from the known pattern")
    return p.parse_args()


def main():
    args = parse_args()

    try:
        mod = importlib.import_module(args.company)
    except ModuleNotFoundError:
        sys.exit(f"No parser found for company '{args.company}' "
                  f"(expected scripts/{args.company}.py)")

    if args.years:
        years = sorted(args.years)
    else:
        if args.end is None:
            sys.exit("--end is required when using --start")
        years = list(range(args.start, args.end + 1))

    min_year = getattr(mod, "MIN_YEAR", None)
    if min_year is not None:
        skipped = [y for y in years if y < min_year]
        if skipped:
            print(f"[{mod.REGION}] skipping {skipped} (no full-year data before {min_year})\n")
            years = [y for y in years if y >= min_year]

    print(f"=== {mod.REGION} pipeline: {len(years)} year(s) requested: {years} ===\n")

    # --- Stage 1: SCRAPE ---------------------------------------------
    links = None
    discover = getattr(mod, "discover_links", None)
    if discover and not args.skip_scrape:
        print("[scrape] discovering available files...")
        try:
            links = discover()
            print(f"[scrape] discovery complete\n")
        except Exception as e:
            print(f"[scrape] WARNING: discovery failed ({e}); "
                  f"falling back to direct URL construction\n")
            links = None
    else:
        print("[scrape] skipped; using direct URL construction\n")

    # --- Stages 2-4: DOWNLOAD -> PROCESS -> CONVERT, looped per year --
    results = {"ok": [], "failed": []}
    for year in years:
        try:
            mod.build_and_export(year, out_dir=args.out_dir, links=links)
            results["ok"].append(year)
        except Exception as e:
            print(f"[{mod.REGION}] FAILED for {year}: {e}")
            results["failed"].append(year)
        print()

    print("=== Summary ===")
    print(f"  succeeded: {results['ok']}")
    print(f"  failed:    {results['failed']}")
    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
