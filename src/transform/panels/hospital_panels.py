"""Combine BHI, static SEIFA and mapped station weather into quarterly hospital panels.

Current-quarter BHI/weather are outcomes or contemporaneous covariates, not
forecast-time predictors. Lag columns use exact calendar quarters; missing values
are retained, never filled from future observations. SEIFA is a static 2021
snapshot and is not an as-of historical socioeconomic series.
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from transform.panels.LHD_panels import load_lags as _load_lags, number, period, read_csv

DEFAULT_OUTPUT = Path("data/silver/hospital-panels")
DEFAULT_CONFIG = Path("src/transform/panels/hospital_panels.toml")


def load_lags(path: Path = DEFAULT_CONFIG) -> tuple[int, ...]:
    return _load_lags(path)


def build_panels(
    bhi_path: Path = Path("data/silver/linking/hospital_bhi_lags.csv"),
    socioeconomic_path: Path = Path("data/silver/linking/hospital_socioeconomic.csv"),
    weather_path: Path = Path("data/silver/bom/station_quarters.csv"),
    mapping_path: Path = Path("data/reference/hospital_weather_mapping.csv"),
    reference_path: Path = Path("data/reference/nsw_hospitals.csv"),
    output_dir: Path = DEFAULT_OUTPUT,
    lags: tuple[int, ...] | None = None,
) -> dict[str, int]:
    """Write one CSV per hospital; return filenames and row counts.

    Station columns preserve all numeric weather/coverage features. Approved
    mappings are deduplicated across observation groups. Stations
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
    refs = {r["hospital_name"]: r for r in reference}
    if len(refs) != len(reference) or len({r['bhi_hospital_id'] for r in reference}) != len(reference):
        raise ValueError("Duplicate hospital reference")
    socioeconomic = {r["hospital_name"]: r for r in socio}
    if len(socioeconomic) != len(socio):
        raise ValueError("Duplicate socioeconomic hospital")
    if set(refs) != set(socioeconomic) or {r['hospital_name'] for r in bhi} != set(refs):
        raise ValueError("hospital names must match across reference, BHI and socioeconomic inputs")
    bcols = [c for c in bf if c not in {"hospital_name", "year", "quarter"}]
    scols = [c for c in sf if c not in {"hospital_name", "bhi_hospital_id"}]
    wcols = [c for c in wf if c not in {
        "bom_station_id", "station_name", "year", "quarter",
        "first_observation_local", "last_observation_local",
    }]
    observations = {}
    for row in bhi:
        key = row['hospital_name'], period(row)
        if key in observations:
            raise ValueError(f"Duplicate BHI hospital-quarter: {key}")
        observations[key] = {c: number(row[c]) for c in bcols}
    stations = {}
    for row in weather:
        key = row['bom_station_id'].zfill(6), period(row)
        if key in stations:
            raise ValueError(f"Duplicate station-quarter: {key}")
        stations[key] = {c: number(row[c]) for c in wcols}
    known_ids = {r['bhi_hospital_id'] for r in reference}
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", hospital) for hospital in known_ids):
        raise ValueError("Hospital identifiers must be safe filename components")
    names_by_id = {r['bhi_hospital_id']: r['hospital_name'] for r in reference}
    mapped = {hospital: set() for hospital in known_ids}
    for row in mapping:
        hospital = row['bhi_hospital_id']
        if not hospital:
            raise ValueError("Mapped hospital ID must not be blank")
        if hospital not in known_ids:
            raise ValueError(f"Unknown mapped hospital: {hospital}")
        if row['hospital_name'] != names_by_id[hospital]:
            raise ValueError(f'Conflicting mapped hospital name: {hospital}')
        if row['mapping_status'] == 'approved' and row['bom_station_id']:
            mapped[hospital].add(row['bom_station_id'].zfill(6))
    panels = {}
    for name, ref in sorted(refs.items()):
        hospital = ref['bhi_hospital_id']
        if not mapped[hospital]:
            raise ValueError(f"No approved weather stations for {hospital}")
        if socioeconomic[name]['bhi_hospital_id'] != ref['bhi_hospital_id']:
            raise ValueError(f"Socioeconomic hospital ID mismatch: {name}")
        calendar = {q for n, q in observations if n == name}
        calendar.update(q for station, q in stations if station in mapped[hospital])
        dynamic = [*bcols, *(f'weather__{s}__{c}' for s in sorted(mapped[hospital]) for c in wcols)]
        static = {f'socioeconomic__{c}': number(socioeconomic[name][c]) for c in scols}
        rows = {}
        for q in range(min(calendar), max(calendar) + 1):
            values = dict(observations.get((name, q), {}))
            for station in sorted(mapped[hospital]):
                values.update({f'weather__{station}__{c}': v
                               for c, v in stations.get((station, q), {}).items()})
            rows[q] = values
        output = []
        for q, values in rows.items():
            row = dict(hospital_id=hospital, hospital_name=name, year=q // 4, quarter=q % 4 + 1)
            row.update(static)
            row.update({c: values.get(c, '') for c in dynamic})
            for lag in sorted(lags):
                row.update({f'{c}__lag_{lag}': rows.get(q - lag, {}).get(c, '') for c in dynamic})
            if len(row) != 4 + len(static) + len(dynamic) * (1 + len(lags)):
                raise ValueError("Feature column name collision")
            output.append(row)
        panels[f'{hospital}.csv'] = output
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
    for flag, default in [('bhi', 'data/silver/linking/hospital_bhi_lags.csv'),
                          ('socioeconomic', 'data/silver/linking/hospital_socioeconomic.csv'),
                          ('weather', 'data/silver/bom/station_quarters.csv'),
                          ('mapping', 'data/reference/hospital_weather_mapping.csv'),
                          ('reference', 'data/reference/nsw_hospitals.csv')]:
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
    print(f'Wrote {len(result)} hospital panels, {sum(result.values())} rows to {args.output_dir}')


if __name__ == '__main__':
    main()
