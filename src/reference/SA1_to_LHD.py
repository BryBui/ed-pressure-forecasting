"""Map 2021 NSW SA1 polygons to Local Health District polygons."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile
import warnings

import geopandas as gpd
import pandas as pd


SA1_CODE_FIELDS = ("SA1_CODE_2021", "SA1_CODE21", "sa1_code_2021", "sa1_code")
STATE_CODE_FIELDS = ("STATE_CODE_2021", "STE_CODE21", "state_code_2021", "state_code")
LHD_CODE_FIELDS = ("lhd_code", "LHD_CODE")
LHD_NAME_FIELDS = ("lhd_name", "LHD_NAME")
OUTPUT_FIELDS = (
    "sa1_code_2021",
    "lhd_code",
    "lhd_name",
    "overlap_pct",
    "assignment_method",
)
EXCLUSION_FIELDS = ("sa1_code_2021", "exclusion_reason")
ASSIGNMENT_METHOD = "largest_area_overlap"
AREA_CRS = "EPSG:3577"
# Nonspatial NSW records present in the official 2021 ABS boundary archive.
NONSPATIAL_NSW_2021 = {"19797979991", "19797979992", "19797979993", "19999949999"}


def _field(
    columns: pd.Index, candidates: tuple[str, ...], label: str, *, required: bool = True
) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    if required:
        raise ValueError(f"Missing {label}; expected one of {candidates}")
    return None


def _prepare_geometry(layer: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    if layer.empty:
        raise ValueError(f"{label} contains no features")
    if layer.crs is None:
        raise ValueError(f"{label} has no coordinate reference system")
    if layer.geometry.isna().any() or layer.geometry.is_empty.any():
        raise ValueError(f"{label} contains missing or empty geometry")
    unsupported = ~layer.geometry.geom_type.isin(("Polygon", "MultiPolygon"))
    if unsupported.any():
        types = ", ".join(sorted(layer.loc[unsupported].geometry.geom_type.unique()))
        raise ValueError(f"{label} contains non-polygon geometry: {types}")

    prepared = layer.copy()
    if (~prepared.geometry.is_valid).any():
        prepared.geometry = prepared.geometry.make_valid()
    if (~prepared.geometry.is_valid).any() or prepared.geometry.is_empty.any():
        raise ValueError(f"{label} contains geometry that could not be made valid")
    return prepared.to_crs(AREA_CRS)


def map_sa1_to_lhds(
    sa1_layer: gpd.GeoDataFrame,
    lhd_layer: gpd.GeoDataFrame,
    *,
    state_code: str = "1",
) -> pd.DataFrame:
    """Assign NSW SA1s by largest overlap and attach exclusions in DataFrame attrs."""
    exclusions: list[dict[str, str]] = []
    state_field = _field(
        sa1_layer.columns, STATE_CODE_FIELDS, "ABS state code", required=False
    )
    if state_field:
        sa1_layer = sa1_layer.loc[
            sa1_layer[state_field].astype(str).str.strip() == state_code
        ].copy()

    sa1_code_field = _field(sa1_layer.columns, SA1_CODE_FIELDS, "2021 SA1 code")
    lhd_code_field = _field(lhd_layer.columns, LHD_CODE_FIELDS, "LHD code")
    lhd_name_field = _field(lhd_layer.columns, LHD_NAME_FIELDS, "LHD name")

    sa1 = sa1_layer[[sa1_code_field, "geometry"]].rename(
        columns={sa1_code_field: "sa1_code_2021"}
    )
    lhds = lhd_layer[[lhd_code_field, lhd_name_field, "geometry"]].rename(
        columns={lhd_code_field: "lhd_code", lhd_name_field: "lhd_name"}
    )
    sa1["sa1_code_2021"] = sa1["sa1_code_2021"].astype(str).str.strip()
    nonspatial = sa1["sa1_code_2021"].isin(NONSPATIAL_NSW_2021) & (
        sa1.geometry.isna() | sa1.geometry.is_empty
    )
    if nonspatial.any():
        exclusions.extend(
            {"sa1_code_2021": code, "exclusion_reason": "nonspatial_record"}
            for code in sa1.loc[nonspatial, "sa1_code_2021"]
        )
        warnings.warn(
            "Excluded nonspatial 2021 NSW SA1 records (no polygon to assign): "
            + ", ".join(sa1.loc[nonspatial, "sa1_code_2021"]),
            stacklevel=2,
        )
        sa1 = sa1.loc[~nonspatial].copy()
    lhds["lhd_code"] = lhds["lhd_code"].astype(str).str.strip()
    lhds["lhd_name"] = lhds["lhd_name"].astype(str).str.strip()

    invalid_codes = ~sa1["sa1_code_2021"].str.fullmatch(r"\d{11}")
    if invalid_codes.any():
        invalid = sa1.loc[invalid_codes, "sa1_code_2021"].iloc[0]
        raise ValueError(f"SA1 code must contain exactly 11 digits: {invalid!r}")
    if sa1["sa1_code_2021"].duplicated().any():
        duplicate = sa1.loc[
            sa1["sa1_code_2021"].duplicated(), "sa1_code_2021"
        ].iloc[0]
        raise ValueError(f"SA1 layer contains duplicate code {duplicate}")
    if lhds["lhd_code"].eq("").any() or lhds["lhd_name"].eq("").any():
        raise ValueError("LHD layer contains a blank code or name")
    if lhds["lhd_code"].duplicated().any():
        duplicate = lhds.loc[lhds["lhd_code"].duplicated(), "lhd_code"].iloc[0]
        raise ValueError(f"LHD layer contains duplicate code {duplicate}")

    sa1 = _prepare_geometry(gpd.GeoDataFrame(sa1), "SA1 layer")
    lhds = _prepare_geometry(gpd.GeoDataFrame(lhds), "LHD layer")
    sa1_areas = sa1.set_index("sa1_code_2021").geometry.area

    intersections = gpd.overlay(
        sa1,
        lhds,
        how="intersection",
        keep_geom_type=False,
        make_valid=True,
    )
    intersections["intersection_area"] = intersections.geometry.area
    intersections = intersections.loc[intersections["intersection_area"] > 0].copy()
    intersections["sa1_area"] = intersections["sa1_code_2021"].map(sa1_areas)

    mapped_codes = set(intersections["sa1_code_2021"])
    unmatched = sorted(set(sa1["sa1_code_2021"]) - mapped_codes)
    if unmatched:
        exclusions.extend(
            {"sa1_code_2021": code, "exclusion_reason": "no_positive_area_lhd_overlap"}
            for code in unmatched
        )
        warnings.warn(
            f"Excluded {len(unmatched)} NSW SA1 polygons with no positive-area "
            "LHD overlap: " + ", ".join(unmatched),
            stacklevel=2,
        )

    selected = (
        intersections.sort_values(
            ["sa1_code_2021", "intersection_area", "lhd_code"],
            ascending=[True, False, True],
        )
        .drop_duplicates("sa1_code_2021", keep="first")
        .copy()
    )
    selected["overlap_pct"] = (
        100 * selected["intersection_area"] / selected["sa1_area"]
    ).map(lambda value: f"{value:.6f}")
    selected["assignment_method"] = ASSIGNMENT_METHOD
    mapping = pd.DataFrame(selected.loc[:, OUTPUT_FIELDS]).reset_index(drop=True)
    mapping.attrs["exclusions"] = sorted(exclusions, key=lambda row: row["sa1_code_2021"])
    return mapping


def read_sa1_layer(path: Path) -> gpd.GeoDataFrame:
    """Read GeoJSON, an extracted shapefile, or a ZIP containing one shapefile.

    Read ZIP members through GDAL without extracting or modifying raw data.
    Reject ambiguous archives rather than silently choosing a layer.
    """
    if path.suffix.lower() != ".zip":
        return gpd.read_file(path)
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".shp")]
    if len(members) != 1:
        raise ValueError("SA1 ZIP must contain exactly one shapefile (.shp)")
    return gpd.read_file(f"/vsizip/{path.resolve().as_posix()}/{members[0]}")


def build_mapping(
    sa1_path: Path, lhd_path: Path, output_path: Path,
    excluded_output_path: Path | None = None,
    boundaries_output_path: Path | None = None,
) -> int:
    """Write mapping, exclusions, and NSW boundaries, replacing each atomically."""
    if excluded_output_path is None:
        excluded_output_path = output_path.with_name(f"{output_path.stem}_excluded.csv")
    if boundaries_output_path is None:
        boundaries_output_path = output_path.with_name("nsw_sa1_boundaries.geojson")
    destinations = (output_path, excluded_output_path, boundaries_output_path)
    if len({path.resolve() for path in destinations}) != len(destinations):
        raise ValueError("Mapping, exclusions, and boundaries must have different output paths")
    sa1_layer = read_sa1_layer(sa1_path)
    mapping = map_sa1_to_lhds(sa1_layer, gpd.read_file(lhd_path))
    export_nsw_boundaries(sa1_layer, boundaries_output_path)
    excluded = pd.DataFrame(mapping.attrs["exclusions"], columns=EXCLUSION_FIELDS)
    for frame, destination in ((mapping, output_path), (excluded, excluded_output_path)):
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_name(f".{destination.name}.tmp")
        frame.to_csv(temporary_path, index=False)
        temporary_path.replace(destination)
    return len(mapping)


def export_nsw_boundaries(sa1_layer: gpd.GeoDataFrame, output_path: Path) -> int:
    """Export all NSW polygons in WGS84, preserving ABS attributes.

    Include polygons without an LHD overlap; omit known nonspatial records.
    """
    code_field = _field(sa1_layer.columns, SA1_CODE_FIELDS, "2021 SA1 code")
    state_field = _field(sa1_layer.columns, STATE_CODE_FIELDS, "ABS state code", required=False)
    nsw = (
        sa1_layer[state_field].astype(str).str.strip().eq("1")
        if state_field else sa1_layer[code_field].astype(str).str.startswith("1")
    )
    boundaries = sa1_layer.loc[nsw].copy()
    nonspatial = boundaries[code_field].astype(str).isin(NONSPATIAL_NSW_2021) & (
        boundaries.geometry.isna() | boundaries.geometry.is_empty
    )
    boundaries = _prepare_geometry(boundaries.loc[~nonspatial], "NSW SA1 boundaries")
    boundaries = boundaries.to_crs("EPSG:4326")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.stem}.tmp.geojson")
    try:
        boundaries.to_file(temporary_path, driver="GeoJSON", index=False)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(boundaries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map 2021 NSW SA1 polygons to Local Health Districts."
    )
    parser.add_argument(
        "--sa1", required=True, type=Path,
        help="2021 SA1 GeoJSON, shapefile (.shp), or shapefile ZIP",
    )
    parser.add_argument(
        "--lhd",
        type=Path,
        default=Path("data/reference/nsw_lhd_boundaries.geojson"),
        help="LHD GeoJSON (default: data/reference/nsw_lhd_boundaries.geojson)",
    )
    parser.add_argument(
        "--boundaries-output", type=Path,
        help="NSW GeoJSON (default: nsw_sa1_boundaries.geojson beside the mapping)",
    )
    parser.add_argument(
        "--excluded-output", type=Path,
        help="Exclusions CSV (default: <output stem>_excluded.csv beside the mapping)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/reference/sa1_to_lhd.csv"),
        help="Output CSV (default: data/reference/sa1_to_lhd.csv)",
    )
    arguments = parser.parse_args()
    count = build_mapping(
        arguments.sa1, arguments.lhd, arguments.output, arguments.excluded_output,
        arguments.boundaries_output,
    )
    print(f"Mapped {count} NSW SA1 polygons to LHDs: {arguments.output}")
    excluded_path = arguments.excluded_output or arguments.output.with_name(
        f"{arguments.output.stem}_excluded.csv"
    )
    print(f"Exclusions: {excluded_path}")
    print(f"NSW boundaries: {arguments.boundaries_output or arguments.output.with_name('nsw_sa1_boundaries.geojson')}")


if __name__ == "__main__":
    main()
