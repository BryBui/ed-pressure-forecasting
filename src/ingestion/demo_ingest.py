"""Manually demonstrate raw ingestion: python -m ingestion.demo_ingest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ingestion.bhi import DOWNLOADS_PAGE, ingest_latest_ed
from ingestion.bom import (
    PUBLIC_STATION_CATALOG_URL,
    PUBLIC_STATION_URL,
    ingest_public_observations,
    load_approved_weather_mapping,
)
from ingestion.seifa import LATEST_RELEASE_PAGE, ingest_sa1_release


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "bhi": ("BHI", "BHI emergency-department performance", DOWNLOADS_PAGE),
    "bom": ("BoM", "BoM public NSW station observations", PUBLIC_STATION_CATALOG_URL),
    "seifa": ("ABS SEIFA", "ABS SEIFA at SA1 level", LATEST_RELEASE_PAGE),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["all", *SOURCES], default="all")
    parser.add_argument("--explain-only", action="store_true",
                        help="Describe selected sources without reading mappings or contacting sources.")
    parser.add_argument("--raw-root", type=Path, default=PROJECT_ROOT / "data" / "raw")
    parser.add_argument("--mapping", type=Path,
                        default=PROJECT_ROOT / "data" / "reference" / "hospital_weather_mapping.csv")
    parser.add_argument("--hospitals", type=Path,
                        default=PROJECT_ROOT / "data" / "reference" / "nsw_hospitals.csv")
    args = parser.parse_args(argv)
    raw_root = args.raw_root.resolve()
    selected = list(SOURCES) if args.source == "all" else [args.source]
    ingested_at = datetime.now(timezone.utc)
    failures = []
    print(f"Raw output root: {raw_root}")
    for source in selected:
        label, description, url = SOURCES[source]
        print(f"\n[{label}] {description}\nSource: {url}")
        if source == "bom":
            print(f"Station feed: {PUBLIC_STATION_URL}")
            print(f"Approved mapping: {args.mapping.resolve()}")
            print("Collects rolling 72-hour observations for approved mapped stations.")
        else:
            print("Stores new or revised workbook content; reuses identical snapshots.")
        if args.explain_only:
            continue
        try:
            if source == "bom":
                rows = load_approved_weather_mapping(args.mapping.resolve(), args.hospitals.resolve())
                result = ingest_public_observations(raw_root, ingested_at, rows)
                print(f"Status: {result.status}")
                print(f"Catalogue: {Path(result.catalog_path).resolve()}")
                print(f"Station output directory: {raw_root / 'bom' / 'station_observations'}")
                print(f"Station snapshots: {result.station_snapshot_count}; "
                      f"created: {result.created_count}; unchanged: {result.unchanged_count}")
                print(f"Unsupported station IDs: {', '.join(result.unsupported_station_ids) or 'none'}")
            else:
                ingest = ingest_latest_ed if source == "bhi" else ingest_sa1_release
                result = ingest(raw_root, ingested_at)
                print(f"Status: {result.status}; reporting period: {result.reporting_period}")
                print(f"Workbook source: {result.source_url}")
                print(f"Output: {Path(result.path).resolve()}")
        except Exception as exc:
            # Finish the other selected sources, then report an unsuccessful run.
            print(f"[{label}] FAILED: {exc}")
            failures.append(label)
    if failures:
        print(f"\nFailed sources: {', '.join(failures)}")
        return 1
    print("\nExplanation complete; no sources contacted." if args.explain_only
          else "\nAll selected ingestion checks completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
