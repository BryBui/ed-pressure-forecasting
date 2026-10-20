"""Combine BHI, static SEIFA and mapped station weather into quarterly LHD panels.

Current-quarter BHI/weather are outcomes or contemporaneous covariates, not
forecast-time predictors. Lag columns use exact calendar quarters; missing values
are retained, never filled from future observations. SEIFA is a static 2021
snapshot and is not an as-of historical socioeconomic series.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

DEFAULT_OUTPUT = Path("data/silver/LHD-panels")
DEFAULT_CONFIG = Path("src/transform/panels/lhd_panels.toml")


def load_lags(path: Path = DEFAULT_CONFIG) -> tuple[int, ...]:
    """Read positive, unique quarterly lag hyperparameters."""
    with path.open('rb') as stream:
        lags = tomllib.load(stream)['panel']['lags']
    if (not isinstance(lags, list) or not lags
            or any(type(lag) is not int or lag < 1 for lag in lags)
            or len(set(lags)) != len(lags)):
        raise ValueError('panel.lags must be a nonempty list of unique positive integers')
    return tuple(lags)


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        rows = list(reader)
    if not rows or len(fields) != len(set(fields)):
        raise ValueError(f"Empty input or duplicate columns: {path}")
    return fields, rows


def number(value: str) -> str:
    if value == "":
        return value
    if value in {"True", "False"}:
        return str(int(value == "True"))
    if not math.isfinite(float(value)):
        raise ValueError(f"Nonfinite numeric value: {value}")
    return value


def period(row: dict) -> int:
    year, quarter = int(row["year"]), int(row["quarter"])
    if not 1 <= quarter <= 4 or year < 1:
        raise ValueError(f"Invalid year/quarter: {year}/{quarter}")
    return year * 4 + quarter - 1


def build_panels(
    bhi_path: Path = Path("data/silver/linking/lhd_bhi_lags.csv"),
    socioeconomic_path: Path = Path("data/silver/linking/lhd_socioeconomic.csv"),
    weather_path: Path = Path("data/silver/bom/station_quarters.csv"),
    mapping_path: Path = Path("data/reference/hospital_weather_mapping.csv"),
    reference_path: Path = Path("data/reference/nsw_lhds.csv"),
    output_dir: Path = DEFAULT_OUTPUT,
    lags: tuple[int, ...] | None = None,
) -> dict[str, int]:
    """Write one CSV per LHD; return filenames and row counts.

    Station columns preserve all numeric weather/coverage features. Approved
    mappings are deduplicated across hospitals and observation groups. Stations
    without observations still receive blank columns. No hospital weighting or
    cross-station aggregation is imposed.
    """
    if lags is None:
        lags = load_lags()
    if not lags or any(type(lag) is not int or lag < 1 for lag in lags) or len(set(lags)) != len(lags):
        raise ValueError("Lags must be unique positive quarter offsets")
    bf, bhi = read_csv(bhi_path)
    sf, socio = read_csv(socioeconomic_path)
    wf, weather = read_csv(weather_path)
    _, mapping = read_csv(mapping_path)
    _, reference = read_csv(reference_path)
    refs = {r["lhd_name"]: r for r in reference}
    if len(refs) != len(reference) or len({r['bhi_lhd_id'] for r in reference}) != len(reference):
        raise ValueError("Duplicate LHD reference")
    socioeconomic = {r["lhd_name"]: r for r in socio}
    if len(socioeconomic) != len(socio):
        raise ValueError("Duplicate socioeconomic LHD")
    if set(refs) != set(socioeconomic) or {r['lhd_name'] for r in bhi} != set(refs):
        raise ValueError("LHD names must match across reference, BHI and socioeconomic inputs")
    bcols = [c for c in bf if c not in {"lhd_name", "year", "quarter"}]
    scols = [c for c in sf if c not in {"lhd_name", "lhd_code"}]
    wcols = [c for c in wf if c not in {
        "bom_station_id", "station_name", "year", "quarter",
        "first_observation_local", "last_observation_local",
    }]
    observations = {}
    for row in bhi:
        key = row['lhd_name'], period(row)
        if key in observations:
            raise ValueError(f"Duplicate BHI LHD-quarter: {key}")
        observations[key] = {c: number(row[c]) for c in bcols}
    stations = {}
    for row in weather:
        key = row['bom_station_id'].zfill(6), period(row)
        if key in stations:
            raise ValueError(f"Duplicate station-quarter: {key}")
        stations[key] = {c: number(row[c]) for c in wcols}
    known_ids = {r['bhi_lhd_id'] for r in reference}
    mapped = {lhd: set() for lhd in known_ids}
    for row in mapping:
        lhd = row['geographic_lhd_id']
        if not lhd:  # Non-geographic specialty networks have no LHD membership.
            continue
        if lhd not in known_ids:
            raise ValueError(f"Unknown mapped LHD: {lhd}")
        if row['mapping_status'] == 'approved' and row['bom_station_id']:
            mapped[lhd].add(row['bom_station_id'].zfill(6))
    panels = {}
    for name, ref in sorted(refs.items()):
        lhd = ref['bhi_lhd_id']
        if not mapped[lhd]:
            raise ValueError(f"No approved weather stations for {lhd}")
        if socioeconomic[name]['lhd_code'] != ref['boundary_code']:
            raise ValueError(f"Socioeconomic boundary code mismatch: {name}")
        calendar = {q for n, q in observations if n == name}
        calendar.update(q for station, q in stations if station in mapped[lhd])
        dynamic = [*bcols, *(f'weather__{s}__{c}' for s in sorted(mapped[lhd]) for c in wcols)]
        static = {f'socioeconomic__{c}': number(socioeconomic[name][c]) for c in scols}
        rows = {}
        for q in range(min(calendar), max(calendar) + 1):
            values = dict(observations.get((name, q), {}))
            for station in sorted(mapped[lhd]):
                values.update({f'weather__{station}__{c}': v
                               for c, v in stations.get((station, q), {}).items()})
            rows[q] = values
        output = []
        for q, values in rows.items():
            row = dict(lhd_id=lhd, lhd_name=name, year=q // 4, quarter=q % 4 + 1)
            row.update(static)
            row.update({c: values.get(c, '') for c in dynamic})
            for lag in sorted(lags):
                row.update({f'{c}__lag_{lag}': rows.get(q - lag, {}).get(c, '') for c in dynamic})
            if len(row) != 4 + len(static) + len(dynamic) * (1 + len(lags)):
                raise ValueError("Feature column name collision")
            output.append(row)
        panels[f'{lhd}.csv'] = output
    inputs = {p.resolve() for p in (bhi_path, socioeconomic_path, weather_path, mapping_path, reference_path)}
    if any((output_dir / name).resolve() in inputs for name in panels):
        raise ValueError("Output must differ from inputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, rows in panels.items():
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', newline='', encoding='utf-8', dir=output_dir,
                                    suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(output_dir / filename)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {name: len(rows) for name, rows in panels.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag, default in [('bhi', 'data/silver/linking/lhd_bhi_lags.csv'),
                          ('socioeconomic', 'data/silver/linking/lhd_socioeconomic.csv'),
                          ('weather', 'data/silver/bom/station_quarters.csv'),
                          ('mapping', 'data/reference/hospital_weather_mapping.csv'),
                          ('reference', 'data/reference/nsw_lhds.csv')]:
        parser.add_argument(f'--{flag}', type=Path, default=Path(default))
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--lags', type=int, nargs='+', help='Override panel.lags from the TOML config')
    args = parser.parse_args()
    try:
        result = build_panels(args.bhi, args.socioeconomic, args.weather, args.mapping,
                              args.reference, args.output_dir,
                              tuple(args.lags) if args.lags is not None else load_lags(args.config))
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(f'Wrote {len(result)} LHD panels, {sum(result.values())} rows to {args.output_dir}')


if __name__ == '__main__':
    main()
