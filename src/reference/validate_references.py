"""Build and validate BHI-derived LHD and hospital reference datasets."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .extract_hospitals import HospitalRecord, extract_hospitals
from .extract_lhds import LhdRecord, extract_geographic_lhds, find_latest_bhi_workbook
from .map_hospitals_to_weather import (
    BomStation,
    STATION_ROLES,
    fetch_bom_station_catalogs,
    fetch_official_hospital_locations,
    select_hospital_station_mappings,
    write_pending_hospital_weather_mapping,
)


BOUNDARY_QUERY_URL = (
    "https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/"
    "SAPHARI_Version_LHD_Areas_2/FeatureServer/0/query"
)
EXPECTED_GEOGRAPHIC_LHDS = 15
LHD_FIELDS = [
    "bhi_lhd_id",
    "lhd_name",
    "lhd_short_name",
    "boundary_code",
    "boundary_name",
    "source_workbook",
]
HOSPITAL_FIELDS = list(HospitalRecord.__dataclass_fields__)


def _default_get(url: str, **kwargs: Any) -> Any:
    from curl_cffi import requests

    return requests.get(url, impersonate="chrome", **kwargs)


def _normalise_name(value: str) -> str:
    return " ".join(value.casefold().replace("local health district", "").split())


def fetch_official_boundaries(
    get: Callable[..., Any] = _default_get,
) -> dict[str, Any]:
    """Fetch current NSW Health LHD polygons and attributes as WGS84 GeoJSON."""
    response = get(
        BOUNDARY_QUERY_URL,
        params={
            "where": "1=1",
            "outFields": "lhd_code,lhd_name",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        },
        timeout=60,
    )
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if payload.get("type") != "FeatureCollection" or not payload.get("features"):
        raise RuntimeError("NSW boundary service returned no LHD features")
    return payload


def validate_lhds(
    records: list[LhdRecord], boundary_geojson: dict[str, Any]
) -> list[dict[str, str]]:
    """Require a one-to-one match between BHI and official geographic LHDs."""
    if len(records) != EXPECTED_GEOGRAPHIC_LHDS:
        raise ValueError(
            f"Expected {EXPECTED_GEOGRAPHIC_LHDS} geographic LHDs, found "
            f"{len(records)}"
        )
    if len({record.bhi_lhd_id for record in records}) != len(records):
        raise ValueError("BHI LHD reference contains duplicate identifiers")

    boundaries: dict[str, dict[str, str]] = {}
    for feature in boundary_geojson["features"]:
        properties = feature.get("properties", {})
        name = str(properties.get("lhd_name") or "").strip()
        code = str(properties.get("lhd_code") or "").strip()
        if not name or not code:
            raise ValueError("Official boundary feature is missing its name or code")
        key = _normalise_name(name)
        if key in boundaries:
            raise ValueError(f"Duplicate official LHD boundary name: {name}")
        boundaries[key] = {"boundary_code": code, "boundary_name": name}

    validated: list[dict[str, str]] = []
    for record in records:
        key = _normalise_name(record.lhd_name)
        if key not in boundaries:
            raise ValueError(
                f"BHI LHD has no official geographic boundary: {record.lhd_name}"
            )
        validated.append({**asdict(record), **boundaries[key]})
    if len(boundaries) != len(validated):
        matched = {_normalise_name(row["boundary_name"]) for row in validated}
        missing = sorted(
            boundary["boundary_name"]
            for key, boundary in boundaries.items()
            if key not in matched
        )
        raise ValueError(f"Official boundaries missing from BHI data: {missing}")
    return validated


def validate_hospitals(
    hospitals: list[HospitalRecord],
    lhds: list[dict[str, str]],
    *,
    source_hospital_count: int | None = None,
) -> dict[str, Any]:
    """Validate BHI identifiers and parent relationships without claiming census."""
    errors: list[str] = []
    hospital_ids = [record.bhi_hospital_id for record in hospitals]
    if len(set(hospital_ids)) != len(hospital_ids):
        errors.append("Duplicate BHI hospital identifiers were found")
    lhd_ids = {row["bhi_lhd_id"] for row in lhds}
    for record in hospitals:
        if not record.bhi_hospital_id or not record.hospital_name:
            errors.append("A hospital has an incomplete BHI identity")
        if (
            record.bhi_parent_type == "geographic_lhd"
            and record.geographic_lhd_id not in lhd_ids
        ):
            errors.append(
                f"{record.bhi_hospital_id} references unknown LHD "
                f"{record.geographic_lhd_id}"
            )
        if record.bhi_parent_type not in {"geographic_lhd", "specialty_network"}:
            errors.append(
                f"{record.bhi_hospital_id} has invalid parent type "
                f"{record.bhi_parent_type}"
            )
    latest = sum(record.present_in_latest_quarter == "true" for record in hospitals)
    if latest != len(hospitals):
        errors.append("Hospital reference contains records absent from the latest quarter")
    if errors:
        raise ValueError("; ".join(errors))
    specialty = sum(record.bhi_parent_type == "specialty_network" for record in hospitals)
    source_hospital_count = source_hospital_count or len(hospitals)
    return {
        "status": "passed_with_warnings",
        "scope": "Hospitals present in the latest BHI ED reporting quarter",
        "authoritative_hospital_register_checked": False,
        "hospital_count": len(hospitals),
        "source_unique_hospital_count": source_hospital_count,
        "included_current_hospital_count": latest,
        "excluded_historical_only_hospital_count": (
            source_hospital_count - len(hospitals)
        ),
        "geographic_lhd_hospital_count": len(hospitals) - specialty,
        "specialty_network_hospital_count": specialty,
        "errors": [],
        "warnings": [
            "This is not a complete NSW hospital register; it reflects current BHI ED coverage.",
            "Hospitals absent from BHI's latest quarter are excluded from reference and weather mapping outputs.",
            "Specialty-network hospitals retain their BHI parent and use official point coordinates for weather mapping.",
            "Thunder mappings beyond the preferred 50 km distance are retained as approved regional proxies and reported by count.",
        ],
    }


def _write_csv(rows: list[dict[str, Any]], fields: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        writer = csv.DictWriter(temporary, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def build_reference_data(
    raw_root: Path,
    reference_root: Path,
    *,
    get: Callable[..., Any] = _default_get,
    station_catalogs: dict[str, list[BomStation]] | None = None,
) -> dict[str, str | int]:
    """Extract, validate, and atomically publish BHI reference datasets."""
    workbook_path = find_latest_bhi_workbook(raw_root)
    lhd_records = extract_geographic_lhds(workbook_path)
    all_hospital_records = extract_hospitals(workbook_path, lhd_records)
    hospital_records = [
        record
        for record in all_hospital_records
        if record.present_in_latest_quarter == "true"
    ]
    hospital_rows = [asdict(record) for record in hospital_records]
    with ThreadPoolExecutor(max_workers=3) as executor:
        boundary_future = executor.submit(fetch_official_boundaries, get)
        location_future = executor.submit(
            fetch_official_hospital_locations, hospital_rows, get
        )
        catalog_future = (
            executor.submit(fetch_bom_station_catalogs, get)
            if station_catalogs is None
            else None
        )
        boundaries = boundary_future.result()
        locations = location_future.result()
        if catalog_future is not None:
            station_catalogs = catalog_future.result()

    validated_lhds = validate_lhds(lhd_records, boundaries)
    validation = validate_hospitals(
        hospital_records,
        validated_lhds,
        source_hospital_count=len(all_hospital_records),
    )
    assert station_catalogs is not None
    station_mappings = select_hospital_station_mappings(
        hospital_rows, locations, station_catalogs
    )
    validation.update(
        generated_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_workbook=workbook_path.name,
        lhd_count=len(validated_lhds),
        hospital_location_count=len(locations),
        weather_station_role_count=len(STATION_ROLES),
        weather_mapping_row_count=len(station_mappings),
        selected_weather_station_count=len(
            {row["bom_station_id"] for row in station_mappings.values()}
        ),
        weather_mapping_approved_count=sum(
            row["mapping_status"] == "approved"
            for row in station_mappings.values()
        ),
        weather_mapping_regional_proxy_count=sum(
            row["mapping_quality"] == "regional_proxy"
            for row in station_mappings.values()
        ),
    )

    reference_root = Path(reference_root)
    lhd_path = reference_root / "nsw_lhds.csv"
    hospital_path = reference_root / "nsw_hospitals.csv"
    boundary_path = reference_root / "nsw_lhd_boundaries.geojson"
    mapping_path = reference_root / "hospital_weather_mapping.csv"
    report_path = reference_root / "reference_validation_report.json"
    _write_csv(validated_lhds, LHD_FIELDS, lhd_path)
    _write_csv(hospital_rows, HOSPITAL_FIELDS, hospital_path)
    _write_json(boundaries, boundary_path)
    write_pending_hospital_weather_mapping(
        hospital_rows,
        mapping_path,
        locations,
        station_mappings,
    )
    _write_json(validation, report_path)
    return {
        "lhd_count": len(validated_lhds),
        "hospital_count": len(hospital_records),
        "source_workbook": str(workbook_path),
        "lhd_path": str(lhd_path),
        "hospital_path": str(hospital_path),
        "mapping_path": str(mapping_path),
        "weather_stations_path": str(mapping_path.with_name("weather_stations.geojson")),
        "validation_report_path": str(report_path),
    }
