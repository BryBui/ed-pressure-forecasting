"""Aggregate raw station snapshots into calendar quarters.

Means describe available subdaily observations, not daily means. Coverage is
based on dates with observations; it does not certify full intraday coverage.
Rain trace is cumulative since 9am and must never be summed across observations.
"""

from __future__ import annotations

import argparse
import calendar
import csv
from datetime import datetime
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT_INPUT = Path("data/raw/bom/station_observations")
DEFAULT_OUTPUT = Path("data/silver/bom/station_quarters.csv")
FIELDS = {
    "air_temp": "temperature_c",
    "apparent_t": "apparent_temperature_c",
    "dewpt": "dew_point_c",
    "rel_hum": "relative_humidity_pct",
    "wind_spd_kmh": "wind_speed_kmh",
    "gust_kmh": "wind_gust_kmh",
    "press_msl": "pressure_msl_hpa",
}


def numeric(value: object) -> float | None:
    if value is None or value in ("", "-", "NA"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid observation value: {value!r}") from error
    if not math.isfinite(result):
        raise ValueError(f"Nonfinite observation value: {value!r}")
    return result


def build_quarters(
    input_dir: Path = DEFAULT_INPUT, output_path: Path = DEFAULT_OUTPUT
) -> int:
    """Write station-quarter CSV; latest ingestion wins for duplicate timestamps.

    Snapshot filenames end with an ingestion timestamp, so lexical ordering
    selects the latest snapshot when observations have subsequently changed.
    Quarter assignment uses BoM's supplied station-local observation date.
    """
    paths = sorted(input_dir.rglob("*.json"),
                   key=lambda p: (p.stem.rsplit("_", 1)[-1], str(p)))
    if not paths:
        raise ValueError(f"No station JSON snapshots found in {input_dir}")
    if output_path.resolve() in {p.resolve() for p in paths}:
        raise ValueError("Output must differ from all inputs")
    observations = {}
    for path in paths:
        station_parts = [p.removeprefix("station_id=") for p in path.parts if p.startswith("station_id=")]
        if len(station_parts) != 1 or not station_parts[0].isdigit():
            raise ValueError(f"Missing or invalid station_id partition: {path}")
        station = station_parts[0]
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document.get("observations", {}).get("data")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Missing observations in {path}")
        for row in rows:
            utc = datetime.strptime(row["aifstime_utc"], "%Y%m%d%H%M%S")
            local = datetime.strptime(row["local_date_time_full"], "%Y%m%d%H%M%S")
            observations[station, utc] = (local, row)

    groups = {}
    for (station, utc), (local, row) in sorted(observations.items()):
        key = station, local.year, (local.month - 1) // 3 + 1
        groups.setdefault(key, []).append((local, row))
    header = ["bom_station_id", "station_name", "year", "quarter",
              "first_observation_local", "last_observation_local", "observation_count",
              "observed_day_count", "expected_day_count", "day_coverage_pct", "incomplete_quarter"]
    for label in FIELDS.values():
        header.extend(f"{label}_{stat}" for stat in ("min", "mean", "max", "observation_count"))
    header.extend(["rain_since_9am_max_mm", "rain_trace_observation_count"])
    output = []
    for (station, year, quarter), entries in sorted(groups.items()):
        dates = {local.date() for local, _ in entries}
        expected = sum(calendar.monthrange(year, month)[1]
                       for month in range(quarter * 3 - 2, quarter * 3 + 1))
        times = [local for local, _ in entries]
        result = [station, entries[-1][1].get("name", ""), year, quarter,
                  min(times).isoformat(), max(times).isoformat(), len(entries),
                  len(dates), expected, round(100 * len(dates) / expected, 4),
                  len(dates) < expected]
        for field in FIELDS:
            values = [value for _, row in entries if (value := numeric(row.get(field))) is not None]
            result.extend([min(values), sum(values) / len(values), max(values), len(values)]
                          if values else ["", "", "", 0])
        rain = [value for _, row in entries if (value := numeric(row.get("rain_trace"))) is not None]
        result.extend([max(rain) if rain else "", len(rain)])
        output.append(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                dir=output_path.parent, suffix=".csv.tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(output)
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        count = build_quarters(args.input_dir, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(f"Wrote {count} station-quarter rows to {args.output}")


if __name__ == "__main__":
    main()
