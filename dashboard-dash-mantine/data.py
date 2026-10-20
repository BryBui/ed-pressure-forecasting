"""Read-only geographic view of repository reference artifacts."""
from functools import lru_cache
from pathlib import Path
from threading import Lock

import geopandas as gpd
import pandas as pd

REFERENCE_ROOT = Path(__file__).resolve().parents[1] / 'data' / 'reference'
_LOAD_LOCK = Lock()
FILES = ('nsw_lhd_boundaries.geojson', 'nsw_sa1_boundaries.geojson', 'sa1_to_lhd.csv')


def resolve_reference_root(root):
    root = Path(root)
    current = root.parent / '.pipeline/current/data/reference'
    return current.resolve() if root.name == 'reference' and current.is_dir() else root.resolve()


def load_data(root=REFERENCE_ROOT):
    root = resolve_reference_root(root)
    signature = tuple((root / name).stat().st_mtime_ns for name in FILES)
    # Dash may request inventory and map concurrently during first render.
    with _LOAD_LOCK:
        return _load(str(root), signature)


@lru_cache(maxsize=1)
def _load(root, signature):
    root = Path(root)
    lhds = gpd.read_file(root / FILES[0]).to_crs(3577)
    sa1 = gpd.read_file(root / FILES[1]).to_crs(3577)
    mapping = pd.read_csv(root / FILES[2], dtype={'sa1_code_2021': str, 'lhd_code': str})
    if lhds.lhd_code.duplicated().any() or sa1.SA1_CODE21.duplicated().any():
        raise ValueError('Boundary identifiers must be unique.')
    if mapping.sa1_code_2021.duplicated().any():
        raise ValueError('Each SA1 must have exactly one LHD assignment.')
    if not set(mapping.lhd_code) <= set(lhds.lhd_code):
        raise ValueError('The correspondence contains unknown LHD codes.')
    if not set(mapping.sa1_code_2021) <= set(sa1.SA1_CODE21):
        raise ValueError('Some mapped SA1 codes have no boundary feature.')
    centers = sa1.geometry.centroid
    coords = pd.DataFrame({'sa1_code_2021': sa1.SA1_CODE21, 'x': centers.x / 1000, 'y': centers.y / 1000})
    mapping = mapping.merge(coords, on='sa1_code_2021', validate='one_to_one')
    if mapping[['x', 'y']].isna().any().any():
        raise ValueError('Mapped SA1 geometry is missing or empty.')
    districts = []
    for row in lhds.itertuples():
        center = row.geometry.centroid
        if center.is_empty:
            raise ValueError(f'Empty LHD geometry: {row.lhd_code}')
        # Simplification affects display only, never assignment or centroid calculation.
        geom = row.geometry.simplify(250, preserve_topology=True)
        polygons = list(geom.geoms) if geom.geom_type == 'MultiPolygon' else [geom]
        x, y = [], []
        for polygon in polygons:
            px, py = polygon.exterior.xy
            x.extend([v / 1000 for v in px] + [None])
            y.extend([v / 1000 for v in py] + [None])
        districts.append({'code': row.lhd_code, 'name': row.lhd_name, 'x': center.x / 1000,
                          'y': center.y / 1000, 'outline_x': x, 'outline_y': y})
    excluded_path = root / 'sa1_to_lhd_excluded.csv'
    excluded = len(pd.read_csv(excluded_path)) if excluded_path.exists() else None
    return {'districts': districts, 'mapping': mapping, 'excluded': excluded,
            'loaded_signature': signature}


WEATHER_FILES = ('hospital_weather_mapping.csv', 'weather_stations.geojson', 'nsw_lhds.csv')


def load_weather(root=REFERENCE_ROOT):
    root = resolve_reference_root(root)
    signature = tuple((root / name).stat().st_mtime_ns for name in WEATHER_FILES)
    with _LOAD_LOCK:
        return _load_weather(str(root), signature)


@lru_cache(maxsize=1)
def _load_weather(root, signature):
    root = Path(root)
    mapping = pd.read_csv(root / WEATHER_FILES[0], dtype=str, keep_default_na=False)
    bridge = pd.read_csv(root / WEATHER_FILES[2], dtype=str, keep_default_na=False)
    if bridge.bhi_lhd_id.duplicated().any() or bridge.boundary_code.duplicated().any():
        raise ValueError('LHD identifier correspondence must be one-to-one.')
    approved = mapping.loc[mapping.mapping_status == 'approved'].copy()
    unassigned = approved.loc[approved.geographic_lhd_id == '', 'bhi_hospital_id'].nunique()
    linked = approved.loc[approved.geographic_lhd_id != ''].merge(
        bridge[['bhi_lhd_id', 'boundary_code']], left_on='geographic_lhd_id',
        right_on='bhi_lhd_id', how='left', validate='many_to_one')
    if linked.boundary_code.isna().any():
        raise ValueError('Weather mapping contains unknown geographic LHD identifiers.')
    stations = gpd.read_file(root / WEATHER_FILES[1]).to_crs(3577)
    if stations.bom_station_id.duplicated().any():
        raise ValueError('Weather station identifiers must be unique.')
    if not stations.geometry.geom_type.eq('Point').all() or stations.geometry.is_empty.any() or stations.geometry.isna().any():
        raise ValueError('Weather stations must have nonempty point geometry.')
    coords = pd.DataFrame({'bom_station_id': stations.bom_station_id,
                           'x': stations.geometry.x / 1000, 'y': stations.geometry.y / 1000})
    linked = linked.merge(coords, on='bom_station_id', how='left', validate='many_to_one')
    if linked[['x', 'y']].isna().any().any():
        raise ValueError('A linked weather station is missing from weather_stations.geojson.')
    return {'rows': linked, 'unassigned_hospitals': unassigned,
            'unapproved_rows': len(mapping) - len(approved)}


def district_stations(weather, selected):
    """One rendered endpoint per station, retaining all hospital/role provenance."""
    rows = weather['rows'].loc[weather['rows'].boundary_code == selected]
    return rows.groupby('bom_station_id', sort=True).agg(
        x=('x', 'first'), y=('y', 'first'), name=('bom_station_name', 'first'),
        hospitals=('hospital_name', lambda values: ', '.join(sorted(set(values)))),
        hospital_count=('bhi_hospital_id', 'nunique'),
        groups=('weather_observation_group', lambda values: ', '.join(sorted(set(values))))
    ).reset_index()
