"""Build a hospital quarterly BHI panel of current-quarter measures.

Current-quarter value columns are potential targets, not forecast-time inputs.
The historical filename is retained; this export does not generate lag features.
Measure names/definitions are never reconciled implicitly across reporting changes.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from transform.bhi.pressure_index import DEFAULT_WEIGHTS, calculate_pressure_index, load_weights

DEFAULT_INPUT = Path("data/silver/bhi")
DEFAULT_REFERENCE = Path("data/reference/nsw_hospitals.csv")
DEFAULT_OUTPUT = Path("data/silver/linking/hospital_bhi_lags.csv")
SOURCES = {
    "overall": "overall_results.csv",
    "separation": "results_by_separation.csv",
    "triage": "results_by_triage.csv",
}
SERIES = ("reporting_category", "subcategory", "measure_name", "metric")
MONTHS = {"Jan-Mar": 0, "Apr-Jun": 1, "Jul-Sep": 2, "Oct-Dec": 3}


def quarter_index(period: str) -> int:
    match = re.fullmatch(r"(Jan-Mar|Apr-Jun|Jul-Sep|Oct-Dec) (\d{4})", period)
    if not match:
        raise ValueError(f"Invalid reporting period: {period!r}")
    return int(match[2]) * 4 + MONTHS[match[1]]


def build_hospital_lags(
    input_dir: Path = DEFAULT_INPUT,
    reference_path: Path = DEFAULT_REFERENCE,
    output_path: Path = DEFAULT_OUTPUT,
    weights_path: Path = DEFAULT_WEIGHTS,
) -> tuple[int, int]:
    """Write a wide panel; blanks represent missing/suppressed values, never zero.

    Columns identify source/category/subcategory/measure/unit. Text notes are
    omitted from this panel and remain available in the source CSVs.
    Every hospital receives the full observed calendar, including absent quarters.
    pressure_index is a configurable 0–1 current-quarter target (higher is worse).
    Return the number of panel rows and distinct measure series.
    """
    weights = load_weights(weights_path)
    inputs = [weights_path, reference_path, *(input_dir / name for name in SOURCES.values())]
    if output_path.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("Output must differ from all inputs")
    with reference_path.open(newline="", encoding="utf-8-sig") as stream:
        reference = list(csv.DictReader(stream))
    hospitals = {row["bhi_hospital_id"]: row["hospital_name"] for row in reference}
    if not hospitals or "" in hospitals or len(hospitals) != len(reference):
        raise ValueError("Hospital reference must contain unique nonempty identifiers")

    columns = {}
    observations = {}
    quarters = set()
    for source, filename in SOURCES.items():
        with (input_dir / filename).open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            required = {"reporting_level", "entity_id", "reporting_period", "measure_value", *SERIES}
            if missing := required - set(reader.fieldnames or ()):
                raise ValueError(f"{filename} missing columns: {sorted(missing)}")
            found = False
            for row in reader:
                if row["reporting_level"] != "Hospital" or row["entity_id"] not in hospitals:
                    continue
                found = True
                quarter = quarter_index(row["reporting_period"])
                quarters.add(quarter)
                identity = (source, *(row[field] for field in SERIES))
                column = "__".join(
                    re.sub(r"[^a-z0-9]+", "_", part.lower().replace("%", "percent")).strip("_") or "total"
                    for part in identity
                )
                if column in columns and columns[column] != identity:
                    raise ValueError(f"Series column collision: {column}")
                columns[column] = identity
                key = (row["entity_id"], quarter, column)
                if key in observations:
                    raise ValueError(f"Duplicate hospital-quarter-measure: {key}")
                value = row["measure_value"].strip()
                if value:
                    try:
                        valid = math.isfinite(float(value))
                    except ValueError:
                        valid = False
                    if not valid:
                        raise ValueError(f"Invalid numeric measure value {value!r}: {key}")
                observations[key] = value
            if not found:
                raise ValueError(f"No hospital observations in {filename}")

    measures = sorted(columns)
    header = ["hospital_name", "year", "quarter", *measures, "pressure_index"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    count = 0
    try:
        with NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=output_path.parent,
                                suffix=".csv.tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(header)
            for entity_id, name in sorted(hospitals.items()):
                for period in range(min(quarters), max(quarters) + 1):
                    year, quarter = divmod(period, 4)
                    values = [name, year, quarter + 1]
                    values.extend(observations.get((entity_id, period, column), "") for column in measures)
                    current = dict(zip(measures, values[3:]))
                    values.append(calculate_pressure_index(current, weights))
                    writer.writerow(values)
                    count += 1
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return count, len(measures)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--hospital-reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pressure-weights", type=Path, default=DEFAULT_WEIGHTS)
    args = parser.parse_args()
    try:
        rows, measures = build_hospital_lags(args.input_dir, args.hospital_reference, args.output, args.pressure_weights)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Wrote {rows} hospital-quarter rows, {measures} measure series to {args.output}")


if __name__ == "__main__":
    main()
