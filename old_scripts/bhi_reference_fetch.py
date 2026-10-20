"""Refresh BHI data and build a measure-definition availability reference.

The reference grain is one dataset/result-group/measure/category/subcategory/
metric combination. It keeps historical and discontinued definitions visible,
while recording how many rows contain usable numeric measure values.
"""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
import re
import sys
from typing import Any

import pandas as pd

from .old_scripts import bhi_data_fetcher

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from health_intelligence.identifiers import (  # noqa: E402
    bhi_definition_id,
    bhi_measure_id,
)

if __package__:
    from .old_scripts import bhi_to_csv
else:
    import old_scripts.bhi_data_fetcher as bhi_data_fetcher
    import old_scripts.bhi_to_csv as bhi_to_csv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "1.raw" / "bhi_data"
DEFAULT_INTERIM_DIR = PROJECT_ROOT / "data" / "2.interim" / "bhi"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "4.reference" / "bhi_measure_reference.csv"
)
DEFAULT_GOVERNANCE_PATH = (
    PROJECT_ROOT / "data" / "4.reference" / "bhi_measure_governance.csv"
)

WORKBOOK_STEM_PATTERN = re.compile(
    r"^BHI_DATA_HQ_(AMB|ED)_Jan2010(Mar|Jun|Sep|Dec)(\d{4})$"
)
RESULT_SHEET_PATTERN = re.compile(r"^(Overall_results|Results_by_.+)$")
PUBLICATION_DATE_PATTERN = re.compile(r"Publication date:\s*(.+)", re.IGNORECASE)
QUARTER_NUMBER = {"Jan": 1, "Apr": 2, "Jul": 3, "Oct": 4}
END_MONTH_QUARTER = {"Mar": 1, "Jun": 2, "Sep": 3, "Dec": 4}
DATASET_NAMES = {"AMB": "Ambulance", "ED": "Emergency Department"}
DEFINITION_COLUMNS = [
    "measure_name",
    "reporting_category",
    "subcategory",
    "metric",
]
REQUIRED_RESULT_COLUMNS = {
    "reporting_period",
    "reporting_level",
    "entity_id",
    "measure_name",
    "reporting_category",
    "subcategory",
    "metric",
    "measure_value",
}
GOVERNANCE_FIELDS = [
    "canonical_measure_id",
    "formal_description",
    "calculation_method",
    "replacement_definition_id",
    "history_comparability",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "governance_notes",
]
GOVERNANCE_IDENTITY_FIELDS = [
    "definition_id",
    "measure_id",
    "dataset_code",
    "source_sheet",
    *DEFINITION_COLUMNS,
]
COMPARABILITY_VALUES = {"unreviewed", "comparable", "not_comparable", "partial"}
REVIEW_STATUS_VALUES = {"unreviewed", "reviewed", "approved"}


def parse_reporting_period(label: str) -> pd.Period:
    """Convert a BHI label such as ``Apr-Jun 2026`` to a quarter."""
    try:
        return pd.Period(
            year=int(str(label)[-4:]),
            quarter=QUARTER_NUMBER[str(label)[:3]],
            freq="Q-DEC",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid BHI reporting period: {label!r}") from error


def workbook_details(stem: str) -> tuple[str, pd.Period]:
    """Return dataset code and advertised final quarter from a workbook stem."""
    match = WORKBOOK_STEM_PATTERN.fullmatch(stem)
    if not match:
        raise ValueError(f"Unrecognised BHI workbook name: {stem!r}")
    dataset_code, end_month, year = match.groups()
    return dataset_code, pd.Period(
        year=int(year), quarter=END_MONTH_QUARTER[end_month], freq="Q-DEC"
    )


def refresh_interim_data(raw_dir: Path, interim_dir: Path) -> list[Path]:
    """Download the two current workbooks and convert only those workbooks."""
    workbooks = bhi_data_fetcher.download_latest_datasets(raw_dir)
    interim_dir.mkdir(parents=True, exist_ok=True)
    converted: list[Path] = []
    for workbook in workbooks:
        print(f"Converting current workbook {workbook.name}...")
        converted.extend(bhi_to_csv.convert_workbook(workbook, interim_dir))
    return converted


def discover_latest_csvs(interim_dir: Path) -> list[Path]:
    """Find every sheet belonging to the newest local AMB and ED extracts."""
    by_dataset: dict[str, dict[str, list[Path]]] = {"AMB": {}, "ED": {}}
    for path in interim_dir.glob("BHI_DATA_HQ_*__*.csv"):
        stem, separator, _ = path.stem.partition("__")
        if not separator:
            continue
        try:
            dataset_code, _ = workbook_details(stem)
        except ValueError:
            continue
        by_dataset[dataset_code].setdefault(stem, []).append(path)

    selected: list[Path] = []
    for dataset_code, extracts in by_dataset.items():
        if not extracts:
            raise FileNotFoundError(
                f"No converted {dataset_code} BHI extracts found in {interim_dir}."
            )
        latest_stem = max(
            extracts,
            key=lambda stem: workbook_details(stem)[1].ordinal,
        )
        selected.extend(extracts[latest_stem])
    return sorted(selected)


def result_csvs(csv_paths: list[Path]) -> list[Path]:
    """Return result sheets, excluding workbook Fields and Metadata sheets."""
    results = []
    for path in csv_paths:
        _, separator, sheet_name = path.stem.partition("__")
        if separator and RESULT_SHEET_PATTERN.fullmatch(sheet_name):
            results.append(path)
    if not results:
        raise FileNotFoundError("No BHI result-sheet CSVs were found.")
    return sorted(results)


def publication_date(csv_paths: list[Path], workbook_stem: str) -> str:
    """Read the workbook publication date from its converted Metadata sheet."""
    metadata_path = next(
        (
            path
            for path in csv_paths
            if path.stem == f"{workbook_stem}__Metadata"
        ),
        None,
    )
    if metadata_path is None:
        return ""
    try:
        metadata = pd.read_csv(metadata_path, header=None, dtype=str).fillna("")
    except pd.errors.EmptyDataError:
        return ""
    for value in metadata.to_numpy().ravel():
        if match := PUBLICATION_DATE_PATTERN.search(value):
            return match.group(1).strip()
    return ""


def _text_values(values: pd.Series) -> str:
    """Join distinct, non-empty text values in a stable order."""
    normalised = {
        str(value).strip()
        for value in values.dropna()
        if str(value).strip()
    }
    return " | ".join(sorted(normalised))


def _period_text(period: pd.Period | None) -> str:
    return "" if period is None else str(period)


def summarise_result_file(
    path: Path,
    all_csv_paths: list[Path],
) -> list[dict[str, Any]]:
    """Create availability records for every definition in one result sheet."""
    workbook_stem, _, sheet_name = path.stem.partition("__")
    dataset_code, workbook_period = workbook_details(workbook_stem)
    data = pd.read_csv(path, low_memory=False)
    missing = REQUIRED_RESULT_COLUMNS - set(data.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")

    for column in DEFINITION_COLUMNS:
        data[column] = data[column].fillna("").astype(str).str.strip()
    data["_period"] = data["reporting_period"].map(parse_reporting_period)
    data["_numeric_value"] = pd.to_numeric(
        data["measure_value"], errors="coerce"
    )
    data["_available"] = data["_numeric_value"].notna()
    publication = publication_date(all_csv_paths, workbook_stem)

    records: list[dict[str, Any]] = []
    grouped = data.groupby(DEFINITION_COLUMNS, dropna=False, sort=True)
    for definition_values, group in grouped:
        definition = dict(zip(DEFINITION_COLUMNS, definition_values, strict=True))
        available = group.loc[group["_available"]]
        first_available = available["_period"].min() if not available.empty else None
        latest_available = available["_period"].max() if not available.empty else None
        latest_reported = group["_period"].max()
        latest_rows = group.loc[group["_period"] == latest_reported]
        workbook_rows = group.loc[group["_period"] == workbook_period]
        data_points = int(group["_available"].sum())
        system_notes = (
            _text_values(group["system_change_note"])
            if "system_change_note" in group
            else ""
        )
        detail_parts = [definition["measure_name"]]
        if definition["reporting_category"]:
            detail_parts.append(definition["reporting_category"])
        if definition["subcategory"]:
            detail_parts.append(definition["subcategory"])
        measure_id = bhi_measure_id(
            dataset_code,
            definition["measure_name"],
            definition["metric"],
        )
        definition_id = bhi_definition_id(
            dataset_code,
            sheet_name,
            *(definition[column] for column in DEFINITION_COLUMNS),
        )
        present_in_latest = not workbook_rows.empty
        current = bool(workbook_rows["_available"].any())
        if current:
            availability_status = "current_available"
        elif present_in_latest:
            availability_status = "current_unavailable"
        elif available.empty:
            availability_status = "unavailable"
        else:
            availability_status = "discontinued_or_stale"

        records.append(
            {
                "definition_id": definition_id,
                "measure_id": measure_id,
                "dataset_code": dataset_code,
                "dataset_name": DATASET_NAMES[dataset_code],
                "source_workbook": f"{workbook_stem}.xlsx",
                "source_sheet": sheet_name.replace("_", " "),
                "publication_date": publication,
                "workbook_latest_quarter": str(workbook_period),
                **definition,
                "measure_definition": " / ".join(detail_parts)
                + f" ({definition['metric']})",
                "reporting_levels": _text_values(group["reporting_level"]),
                "first_reported_quarter": str(group["_period"].min()),
                "latest_reported_quarter": str(latest_reported),
                "first_available_quarter": _period_text(first_available),
                "latest_available_quarter": _period_text(latest_available),
                "effective_start_quarter": _period_text(first_available),
                "effective_end_quarter": (
                    "" if present_in_latest else _period_text(latest_available)
                ),
                "availability_status": availability_status,
                "reported_quarters": int(group["_period"].nunique()),
                "available_quarters": int(available["_period"].nunique()),
                "unique_entities": int(group["entity_id"].nunique()),
                "total_entries": int(len(group)),
                "available_data_points": data_points,
                "unavailable_data_points": int(len(group) - data_points),
                "availability_percent": round(100 * data_points / len(group), 2),
                "latest_reported_quarter_entries": int(len(latest_rows)),
                "latest_reported_quarter_data_points": int(
                    latest_rows["_available"].sum()
                ),
                "workbook_latest_quarter_entries": int(len(workbook_rows)),
                "workbook_latest_quarter_data_points": int(
                    workbook_rows["_available"].sum()
                ),
                "available_in_workbook_latest_quarter": current,
                "quarters_behind_workbook": int(
                    workbook_period.ordinal - latest_reported.ordinal
                ),
                "has_system_change_note": bool(system_notes),
                "system_change_notes": system_notes,
            }
        )
    return records


def build_reference(csv_paths: list[Path]) -> pd.DataFrame:
    """Build the complete AMB and ED reference from selected workbook sheets."""
    records: list[dict[str, Any]] = []
    for path in result_csvs(csv_paths):
        records.extend(summarise_result_file(path, csv_paths))
    reference = pd.DataFrame(records)
    if reference.empty:
        raise ValueError("The BHI measure reference contains no definitions.")
    reference.insert(
        0,
        "reference_generated_at",
        datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    return reference.sort_values(
        [
            "dataset_code",
            "source_sheet",
            "measure_name",
            "reporting_category",
            "subcategory",
            "metric",
        ]
    ).reset_index(drop=True)


def _new_governance_rows(reference: pd.DataFrame) -> pd.DataFrame:
    """Create unreviewed governance rows for definitions not seen before."""
    governance = reference[GOVERNANCE_IDENTITY_FIELDS].drop_duplicates().copy()
    governance["canonical_measure_id"] = governance["measure_id"]
    governance["formal_description"] = ""
    governance["calculation_method"] = ""
    governance["replacement_definition_id"] = ""
    governance["history_comparability"] = "unreviewed"
    governance["review_status"] = "unreviewed"
    governance["reviewed_by"] = ""
    governance["reviewed_at"] = ""
    governance["governance_notes"] = ""
    return governance[[*GOVERNANCE_IDENTITY_FIELDS, *GOVERNANCE_FIELDS]]


def _validate_governance(governance: pd.DataFrame, path: Path) -> None:
    required = {*GOVERNANCE_IDENTITY_FIELDS, *GOVERNANCE_FIELDS}
    missing = required - set(governance.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    if governance["definition_id"].duplicated().any():
        raise ValueError(f"{path.name} contains duplicate definition_id values.")
    invalid_comparability = set(governance["history_comparability"]) - (
        COMPARABILITY_VALUES | {""}
    )
    if invalid_comparability:
        raise ValueError(
            "Invalid history_comparability values: "
            f"{sorted(invalid_comparability)}"
        )
    invalid_status = set(governance["review_status"]) - (
        REVIEW_STATUS_VALUES | {""}
    )
    if invalid_status:
        raise ValueError(f"Invalid review_status values: {sorted(invalid_status)}")


def sync_governance(reference: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Append new definitions while preserving every human-reviewed field."""
    new_rows = _new_governance_rows(reference)
    if path.exists():
        existing = pd.read_csv(path, dtype=str, keep_default_na=False)
        _validate_governance(existing, path)
        additions = new_rows.loc[
            ~new_rows["definition_id"].isin(existing["definition_id"])
        ]
        governance = pd.concat([existing, additions], ignore_index=True)
    else:
        governance = new_rows
    governance = governance.sort_values(
        ["dataset_code", "source_sheet", "measure_name", "reporting_category", "subcategory"]
    ).reset_index(drop=True)
    _validate_governance(governance, path)
    cache_reference(governance, path)
    return governance


def attach_governance(
    reference: pd.DataFrame, governance: pd.DataFrame
) -> pd.DataFrame:
    """Attach reviewed semantics without allowing them to alter source facts."""
    decisions = governance[["definition_id", *GOVERNANCE_FIELDS]]
    return reference.merge(
        decisions, on="definition_id", how="left", validate="one_to_one"
    )


def cache_reference(reference: pd.DataFrame, output_path: Path) -> Path:
    """Atomically replace the dashboard-independent reference CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    try:
        reference.to_csv(temporary_path, index=False)
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="build from the newest local interim extracts without internet access",
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--interim-dir", type=Path, default=DEFAULT_INTERIM_DIR)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--governance-path", type=Path, default=DEFAULT_GOVERNANCE_PATH
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.no_download:
        csv_paths = discover_latest_csvs(args.interim_dir)
        source_mode = "newest local extracts"
    else:
        csv_paths = refresh_interim_data(args.raw_dir, args.interim_dir)
        source_mode = "freshly downloaded extracts"

    reference = build_reference(csv_paths)
    governance = sync_governance(reference, args.governance_path)
    reference = attach_governance(reference, governance)
    output_path = cache_reference(reference, args.output_path)
    current = int(reference["available_in_workbook_latest_quarter"].sum())
    print(
        f"Cached {len(reference)} measure definitions from {source_mode} at "
        f"{output_path}"
    )
    unreviewed = int((governance["review_status"] == "unreviewed").sum())
    print(
        f"Synced {len(governance)} governance rows at {args.governance_path}; "
        f"{unreviewed} remain unreviewed."
    )
    print(f"Definitions available in their workbook's latest quarter: {current}")


if __name__ == "__main__":
    main()
