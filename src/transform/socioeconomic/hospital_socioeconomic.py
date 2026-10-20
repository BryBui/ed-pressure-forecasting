"""Build nearest-hospital SA1 catchments and descriptive 2021 SEIFA summaries.

Distances are Euclidean kilometres from polygon centroids in EPSG:3577.
Catchments are geographic proxies, not observed patient flows or official ABS
hospital indexes. All hospitals in the supplied coordinate CSV are candidates.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from reference.SA1_to_LHD import (
    AREA_CRS, NONSPATIAL_NSW_2021, SA1_CODE_FIELDS, STATE_CODE_FIELDS,
    _field, _prepare_geometry, read_sa1_layer,
)
from transform.socioeconomic.lhd_socioeconomic import _aggregate_groups

DEFAULT_SA1 = Path("data/reference/nsw_sa1_boundaries.geojson")
DEFAULT_HOSPITALS = Path("data/reference/hospital_weather_mapping.csv")
DEFAULT_MAPPING = Path("data/reference/sa1_to_hospital.csv")
DEFAULT_OUTPUT = Path("data/silver/linking/hospital_socioeconomic.csv")


def map_sa1_to_hospitals(sa1: gpd.GeoDataFrame, hospitals: pd.DataFrame) -> pd.DataFrame:
    """Assign each spatial NSW SA1 once; break equal distances by hospital ID."""
    code = _field(sa1.columns, SA1_CODE_FIELDS, "2021 SA1 code")
    state = _field(sa1.columns, STATE_CODE_FIELDS, "ABS state code", required=False)
    nsw = (sa1[state].astype(str).str.strip().eq("1") if state
           else sa1[code].astype(str).str.startswith("1"))
    sa1 = sa1.loc[nsw, [code, "geometry"]].rename(columns={code: "sa1_code_2021"}).copy()
    sa1["sa1_code_2021"] = sa1.sa1_code_2021.astype(str).str.strip()
    nonspatial = sa1.sa1_code_2021.isin(NONSPATIAL_NSW_2021) & (
        sa1.geometry.isna() | sa1.geometry.is_empty)
    sa1 = sa1.loc[~nonspatial].copy()
    if not sa1.sa1_code_2021.str.fullmatch(r"\d{11}").all():
        raise ValueError("SA1 codes must contain exactly 11 digits")
    if sa1.sa1_code_2021.duplicated().any():
        raise ValueError("Duplicate mapped SA1")
    sa1 = _prepare_geometry(sa1, "SA1 layer")
    sa1.geometry = sa1.geometry.centroid

    fields = ["bhi_hospital_id", "hospital_name", "hospital_latitude", "hospital_longitude"]
    if not set(fields).issubset(hospitals.columns):
        raise ValueError(f"Hospital coordinates require {fields}")
    # Weather observations repeat each hospital once per role. Require agreement.
    hospitals = hospitals[fields].copy()
    for field in fields[:2]:
        if hospitals[field].isna().any():
            raise ValueError("Hospital ID and name must not be missing")
        hospitals[field] = hospitals[field].astype(str).str.strip()
        if hospitals[field].eq("").any():
            raise ValueError("Hospital ID and name must not be blank")
    for field, limit in zip(fields[2:], (90, 180)):
        hospitals[field] = pd.to_numeric(hospitals[field], errors="raise")
        if not hospitals[field].between(-limit, limit).all():
            raise ValueError(f"Invalid hospital coordinates: {field}")
    hospitals = hospitals.drop_duplicates()
    if hospitals.empty or hospitals.bhi_hospital_id.duplicated().any():
        raise ValueError("Empty hospital data or conflicting hospital coordinates/names")
    points = gpd.GeoDataFrame(
        hospitals[fields[:2]], geometry=gpd.points_from_xy(
            hospitals.hospital_longitude, hospitals.hospital_latitude), crs="EPSG:4326",
    ).to_crs(AREA_CRS)
    nearest = gpd.sjoin_nearest(sa1, points, how="left", distance_col="distance_m")
    nearest = nearest.sort_values(["sa1_code_2021", "distance_m", "bhi_hospital_id"]).drop_duplicates("sa1_code_2021")
    nearest["distance_km"] = nearest.distance_m / 1000
    nearest["assignment_method"] = "nearest_hospital_sa1_centroid_epsg3577"
    return pd.DataFrame(nearest[["sa1_code_2021", "bhi_hospital_id", "hospital_name", "distance_km", "assignment_method"]]).reset_index(drop=True)


def aggregate_hospitals(mapping_path: Path, seifa_path: Path) -> list[dict]:
    """Apply the same complete-case and population weighting rules as LHDs."""
    return _aggregate_groups(mapping_path, seifa_path, "bhi_hospital_id", "hospital_name")


def build_hospital_socioeconomic(
    sa1_path: Path, hospitals_path: Path, seifa_path: Path,
    mapping_path: Path = DEFAULT_MAPPING, output_path: Path = DEFAULT_OUTPUT,
) -> int:
    """Write the auditable correspondence and one summary per assigned hospital."""
    inputs = {p.resolve() for p in (sa1_path, hospitals_path, seifa_path)}
    if mapping_path.resolve() == output_path.resolve() or any(
        p.resolve() in inputs for p in (mapping_path, output_path)
    ):
        raise ValueError("Outputs must differ from each other and input files")
    mapping = map_sa1_to_hospitals(read_sa1_layer(sa1_path), pd.read_csv(hospitals_path, dtype=str))
    # Validate summaries before replacing either final output.
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = mapping_path.with_name(f".{mapping_path.name}.tmp")
    summary_temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        mapping.to_csv(temporary, index=False)
        rows = aggregate_hospitals(temporary, seifa_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(summary_temporary, index=False)
        temporary.replace(mapping_path)
        summary_temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
        summary_temporary.unlink(missing_ok=True)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sa1", type=Path, default=DEFAULT_SA1)
    parser.add_argument("--hospitals", type=Path, default=DEFAULT_HOSPITALS)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seifa", type=Path)
    args = parser.parse_args()
    if args.seifa is None:
        snapshots = sorted(p for p in Path("data/raw/abs/seifa/census_year=2021").glob("seifa_2021_*.xlsx") if not p.name.endswith(".part.xlsx"))
        if not snapshots:
            parser.error("No local 2021 SEIFA workbook found; supply --seifa")
        args.seifa = snapshots[-1]
    count = build_hospital_socioeconomic(args.sa1, args.hospitals, args.seifa, args.mapping, args.output)
    print(f"Wrote {count} hospital summaries to {args.output}; SA1 links: {args.mapping}")


if __name__ == "__main__":
    main()
