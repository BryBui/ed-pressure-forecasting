"""Create and preserve the hospital-to-weather mapping contract."""

from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any


MYHOSPITALS_QUERY_URL = (
    "https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/"
    "MyHospitals_Public/FeatureServer/0/query"
)
NSW_HEALTH_FACILITIES_QUERY_URL = (
    "https://portal.spatial.nsw.gov.au/server/rest/services/"
    "NSW_FOI_Health_Facilities_multiCRS/FeatureServer/1/query"
)

# BHI and MyHospitals use unrelated identifiers. These reviewed aliases cover
# only names that do not match after punctuation/case normalisation.
MYHOSPITAL_CODE_BY_BHI_ID = {
    "A212": "H0017",
    "A233": "H0033",
    "A237": "H0023",
    "C213": "H0038",
    "C238": "H0015",
    "D204": "H0055",
    "D311": "H0058",
    "H208": "H0129",
    "L201": "H0204",
    "M207": "H0155",
    "N201": "H0135",
    "N211": "H0143",
    "N215": "H0145",
    "N218": "H0167",
    "N219": "H0032",
    "P207": "H0046",
    "R219": "H0180",
    "S201": "H0223",
}
TWEED_VALLEY_BHI_ID = "H223"
TWEED_VALLEY_TOPOID = "505469665"


BOM_STATION_LIST_URL = (
    "https://www.bom.gov.au/climate/data/lists_by_element/alphaAUS_{code}.txt"
)


@dataclass(frozen=True)
class StationRole:
    name: str
    element_codes: tuple[str, ...]
    coverage_basis: str
    minimum_record_years: float
    minimum_completeness_pct: float
    maximum_preferred_distance_km: float


STATION_ROLES = (
    StationRole(
        "temperature",
        ("122", "123"),
        "maximum and minimum air temperature",
        1.0,
        50.0,
        50.0,
    ),
    StationRole("rainfall", ("136",), "daily rainfall", 1.0, 50.0, 50.0),
    StationRole(
        "relative_humidity",
        ("5",),
        "dew point holding used by BoM as the relative-humidity proxy",
        1.0,
        50.0,
        75.0,
    ),
    StationRole(
        "wind",
        ("7", "131"),
        "wind speed and maximum wind gust speed",
        1.0,
        50.0,
        75.0,
    ),
    StationRole(
        "thunderstorms",
        ("12",),
        "total cloud holding used by BoM as the visual-weather proxy",
        5.0,
        50.0,
        50.0,
    ),
)
STATION_ROLE_NAMES = tuple(role.name for role in STATION_ROLES)


@dataclass(frozen=True)
class BomStation:
    station_id: str
    station_name: str
    latitude: float
    longitude: float
    record_start: str
    record_end: str
    record_years: float
    completeness_pct: float


STATION_LINE = re.compile(
    r"^\s*(?P<site>\d+)\s+(?P<name>.*?)\s+"
    r"(?P<latitude>-?\d+\.\d+)\s+(?P<longitude>-?\d+\.\d+)\s+"
    r"(?P<start_month>[A-Z][a-z]{2})\s+(?P<start_year>\d{4})\s+"
    r"(?P<end_month>[A-Z][a-z]{2})\s+(?P<end_year>\d{4})\s+"
    r"(?P<tail>.+)$"
)
MONTH_NUMBER = {
    month: number
    for number, month in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


def parse_bom_station_list(text: str) -> list[BomStation]:
    """Parse one fixed-width BoM element holding list."""
    stations: list[BomStation] = []
    for line in text.splitlines():
        match = STATION_LINE.match(line.rstrip())
        if not match:
            continue
        tail = match.group("tail").split()
        if len(tail) not in {3, 4} or tail[-1] not in {"Y", "N"}:
            continue
        try:
            years = float(tail[0])
            completeness = float(tail[1])
        except ValueError:
            continue
        stations.append(
            BomStation(
                station_id=match.group("site").zfill(6),
                station_name=match.group("name").strip(),
                latitude=float(match.group("latitude")),
                longitude=float(match.group("longitude")),
                record_start=(
                    f"{match.group('start_year')}-{MONTH_NUMBER[match.group('start_month')]:02d}"
                ),
                record_end=(
                    f"{match.group('end_year')}-{MONTH_NUMBER[match.group('end_month')]:02d}"
                ),
                record_years=years,
                completeness_pct=completeness,
            )
        )
    if not stations:
        raise ValueError("BoM element station list contained no parseable stations")
    return stations


def _response_text(response: Any, service_name: str) -> str:
    try:
        response.raise_for_status()
        text = response.text
    finally:
        response.close()
    if not text.strip():
        raise RuntimeError(f"{service_name} returned an empty station list")
    return text


def fetch_bom_station_catalogs(get: Any) -> dict[str, list[BomStation]]:
    """Fetch each official element list needed by the station-role policy."""
    codes = sorted({code for role in STATION_ROLES for code in role.element_codes})

    def fetch(code: str) -> tuple[str, list[BomStation]]:
        response = get(BOM_STATION_LIST_URL.format(code=code), timeout=60)
        return (
            code,
            parse_bom_station_list(_response_text(response, f"BoM element {code}")),
        )

    with ThreadPoolExecutor(max_workers=len(codes)) as executor:
        return dict(executor.map(fetch, codes))


def _haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_km = 6371.0088
    lat_a, lat_b = radians(latitude_a), radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = radians(longitude_b - longitude_a)
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 2 * earth_radius_km * asin(sqrt(value))


def _eligible_stations(
    role: StationRole,
    catalogs: dict[str, list[BomStation]],
) -> list[BomStation]:
    by_code = {
        code: {station.station_id: station for station in catalogs[code]}
        for code in role.element_codes
    }
    station_ids = set.intersection(*(set(rows) for rows in by_code.values()))
    eligible: list[BomStation] = []
    for station_id in station_ids:
        records = [by_code[code][station_id] for code in role.element_codes]
        current_end = max(
            station.record_end
            for code in role.element_codes
            for station in catalogs[code]
        )
        if any(record.record_end != current_end for record in records):
            continue
        years = min(record.record_years for record in records)
        completeness = min(record.completeness_pct for record in records)
        if years < role.minimum_record_years:
            continue
        if completeness < role.minimum_completeness_pct:
            continue
        representative = records[0]
        eligible.append(
            BomStation(
                station_id=station_id,
                station_name=representative.station_name,
                latitude=representative.latitude,
                longitude=representative.longitude,
                record_start=max(record.record_start for record in records),
                record_end=min(record.record_end for record in records),
                record_years=years,
                completeness_pct=completeness,
            )
        )
    if not eligible:
        raise ValueError(f"No eligible active BoM stations for role {role.name}")
    return eligible


def select_hospital_station_mappings(
    hospitals: list[dict[str, Any]],
    locations: dict[str, dict[str, str]],
    catalogs: dict[str, list[BomStation]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Apply the approved policy to select a station for every hospital/role."""
    eligible_by_role = {
        role.name: _eligible_stations(role, catalogs) for role in STATION_ROLES
    }
    proposals: dict[tuple[str, str], dict[str, str]] = {}
    for hospital in hospitals:
        hospital_id = str(hospital["bhi_hospital_id"])
        location = locations[hospital_id]
        hospital_latitude = float(location["hospital_latitude"])
        hospital_longitude = float(location["hospital_longitude"])
        for role in STATION_ROLES:
            station = min(
                eligible_by_role[role.name],
                key=lambda candidate: _haversine_km(
                    hospital_latitude,
                    hospital_longitude,
                    candidate.latitude,
                    candidate.longitude,
                ),
            )
            distance = _haversine_km(
                hospital_latitude,
                hospital_longitude,
                station.latitude,
                station.longitude,
            )
            proposals[(hospital_id, role.name)] = {
                "weather_observation_group": role.name,
                "bom_element_codes": "+".join(role.element_codes),
                "station_coverage_basis": role.coverage_basis,
                "bom_station_id": station.station_id,
                "bom_station_name": station.station_name,
                "station_latitude": f"{station.latitude:.4f}",
                "station_longitude": f"{station.longitude:.4f}",
                "station_record_start": station.record_start,
                "station_record_end": station.record_end,
                "station_record_years": f"{station.record_years:.1f}",
                "station_completeness_pct": f"{station.completeness_pct:.0f}",
                "distance_km": f"{distance:.1f}",
                "mapping_quality": (
                    "preferred_distance"
                    if distance <= role.maximum_preferred_distance_km
                    else "regional_proxy"
                ),
                "mapping_method": "nearest_active_station_with_required_elements",
                "mapping_status": "approved",
            }
    return proposals


MAPPING_FIELDS = [
    "bhi_hospital_id",
    "hospital_name",
    "bhi_parent_name",
    "geographic_lhd_id",
    "hospital_latitude",
    "hospital_longitude",
    "hospital_location_source",
    "hospital_location_id",
    "hospital_location_name",
    "hospital_location_match_method",
    "weather_observation_group",
    "bom_element_codes",
    "station_coverage_basis",
    "bom_station_id",
    "bom_station_name",
    "station_latitude",
    "station_longitude",
    "station_record_start",
    "station_record_end",
    "station_record_years",
    "station_completeness_pct",
    "distance_km",
    "mapping_quality",
    "mapping_method",
    "mapping_status",
]

PRE_QUALITY_MAPPING_FIELDS = [
    field for field in MAPPING_FIELDS if field != "mapping_quality"
]
PRE_ROLE_MAPPING_FIELDS = [
    field
    for field in MAPPING_FIELDS
    if field
    not in {
        "weather_observation_group",
        "bom_element_codes",
        "station_coverage_basis",
        "station_record_start",
        "station_record_end",
        "station_record_years",
        "station_completeness_pct",
        "mapping_quality",
    }
]
LEGACY_MAPPING_FIELDS = [
    field
    for field in PRE_ROLE_MAPPING_FIELDS
    if not field.startswith("hospital_location_")
]


def _normalise_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _response_json(response: Any, service_name: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if payload.get("error"):
        raise RuntimeError(f"{service_name} returned an error: {payload['error']}")
    return payload


def fetch_official_hospital_locations(
    hospitals: list[dict[str, Any]], get: Any
) -> dict[str, dict[str, str]]:
    """Match the BHI-scoped hospitals to official NSW point coordinates."""
    response = get(
        MYHOSPITALS_QUERY_URL,
        params={
            "f": "json",
            "where": "health_system='public' AND operational_status='open'",
            "outFields": (
                "hospital_code,hospital_name,latitude,longitude,health_system,"
                "operational_status"
            ),
            "returnGeometry": "false",
            "resultRecordCount": 1000,
        },
        timeout=60,
    )
    payload = _response_json(response, "MyHospitals")
    sites = [feature.get("attributes", {}) for feature in payload.get("features", [])]
    by_code = {str(site.get("hospital_code") or ""): site for site in sites}
    by_name = {
        _normalise_name(str(site.get("hospital_name") or "")): site
        for site in sites
        if site.get("hospital_name")
    }

    tweed_site: dict[str, Any] | None = None
    if any(
        str(hospital["bhi_hospital_id"]) == TWEED_VALLEY_BHI_ID
        for hospital in hospitals
    ):
        response = get(
            NSW_HEALTH_FACILITIES_QUERY_URL,
            params={
                "f": "json",
                "where": f"topoid={TWEED_VALLEY_TOPOID}",
                "outFields": "topoid,generalname,operationalstatus",
                "returnGeometry": "true",
                "outSR": "4326",
            },
            timeout=60,
        )
        spatial_payload = _response_json(response, "NSW Health Facilities")
        spatial_features = spatial_payload.get("features", [])
        if len(spatial_features) != 1:
            raise RuntimeError("Could not uniquely locate Tweed Valley Hospital")
        tweed_site = spatial_features[0]

    locations: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for hospital in hospitals:
        hospital_id = str(hospital["bhi_hospital_id"])
        hospital_name = str(hospital["hospital_name"])
        if hospital_id == TWEED_VALLEY_BHI_ID:
            assert tweed_site is not None
            attributes = tweed_site.get("attributes", {})
            geometry = tweed_site.get("geometry", {})
            site = {
                "latitude": geometry.get("y"),
                "longitude": geometry.get("x"),
                "location_id": str(attributes.get("topoid") or ""),
                "location_name": str(attributes.get("generalname") or ""),
                "source": "NSW Health Facilities",
                "match_method": "reviewed_alias",
            }
        else:
            official_code = MYHOSPITAL_CODE_BY_BHI_ID.get(hospital_id)
            official = (
                by_code.get(official_code, {})
                if official_code
                else by_name.get(_normalise_name(hospital_name), {})
            )
            site = {
                "latitude": official.get("latitude"),
                "longitude": official.get("longitude"),
                "location_id": str(official.get("hospital_code") or ""),
                "location_name": str(official.get("hospital_name") or ""),
                "source": "MyHospitals",
                "match_method": "reviewed_alias" if official_code else "exact_name",
            }
        if not all(
            site.get(field)
            for field in ("latitude", "longitude", "location_id", "location_name")
        ):
            missing.append(f"{hospital_id} ({hospital_name})")
            continue
        latitude = float(site["latitude"])
        longitude = float(site["longitude"])
        if not (-38.0 <= latitude <= -28.0 and 140.0 <= longitude <= 154.0):
            raise ValueError(f"Hospital coordinates fall outside NSW: {hospital_id}")
        locations[hospital_id] = {
            "hospital_latitude": f"{latitude:.6f}",
            "hospital_longitude": f"{longitude:.6f}",
            "hospital_location_source": str(site["source"]),
            "hospital_location_id": str(site["location_id"]),
            "hospital_location_name": str(site["location_name"]),
            "hospital_location_match_method": str(site["match_method"]),
        }
    if missing:
        raise ValueError("Official hospital locations not found for: " + ", ".join(missing))
    return locations


def write_pending_hospital_weather_mapping(
    hospitals: list[dict[str, Any]],
    output_path: Path,
    locations: dict[str, dict[str, str]] | None = None,
    station_proposals: dict[tuple[str, str], dict[str, str]] | None = None,
) -> Path:
    """Seed five station roles while retaining role-aware approved mappings."""
    existing: dict[tuple[str, str], dict[str, str]] = {}
    pre_role: dict[str, dict[str, str]] = {}
    if output_path.exists():
        with output_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames not in (
                MAPPING_FIELDS,
                PRE_QUALITY_MAPPING_FIELDS,
                PRE_ROLE_MAPPING_FIELDS,
                LEGACY_MAPPING_FIELDS,
            ):
                raise ValueError(
                    f"{output_path.name} must have columns {MAPPING_FIELDS}"
                )
            for row in reader:
                hospital_id = row["bhi_hospital_id"]
                if reader.fieldnames in (MAPPING_FIELDS, PRE_QUALITY_MAPPING_FIELDS):
                    role_name = row["weather_observation_group"]
                    key = (hospital_id, role_name)
                    if key in existing:
                        raise ValueError(
                            "Duplicate hospital weather mapping: "
                            f"{hospital_id}/{role_name}"
                        )
                    existing[key] = row
                elif hospital_id in pre_role:
                    raise ValueError(
                        f"Duplicate hospital weather mapping: {hospital_id}"
                    )
                else:
                    pre_role[hospital_id] = row

    # Rows that are no longer in the latest BHI quarter are intentionally
    # dropped. The raw workbook still preserves their historical records.

    rows: list[dict[str, str]] = []
    for hospital in hospitals:
        hospital_id = str(hospital["bhi_hospital_id"])
        location = (locations or {}).get(hospital_id, {})
        for role in STATION_ROLES:
            key = (hospital_id, role.name)
            previous = existing.get(key) or pre_role.get(hospital_id)
            row = {field: "" for field in MAPPING_FIELDS}
            if previous:
                row.update(previous)
            previous_location = (
                row.get("hospital_latitude", ""),
                row.get("hospital_longitude", ""),
            )
            row.update(
                bhi_hospital_id=hospital_id,
                hospital_name=str(hospital["hospital_name"]),
                bhi_parent_name=str(hospital["bhi_parent_name"]),
                geographic_lhd_id=str(hospital["geographic_lhd_id"]),
                weather_observation_group=role.name,
            )
            row.update(location)
            current_location = (
                row.get("hospital_latitude", ""),
                row.get("hospital_longitude", ""),
            )
            preserve_approval = (
                key in existing
                and row.get("mapping_status") == "approved"
                and previous_location == current_location
            )
            if not preserve_approval:
                proposal = (station_proposals or {}).get(key)
                if proposal:
                    row.update(proposal)
                else:
                    row["mapping_method"] = (
                        "nearest_active_station_with_required_elements"
                    )
                    row["mapping_status"] = (
                        "pending_station" if location else "pending_location"
                    )
            rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        suffix=".tmp",
        dir=output_path.parent,
        delete=False,
    ) as temporary:
        writer = csv.DictWriter(temporary, fieldnames=MAPPING_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        temporary_path = Path(temporary.name)
    temporary_path.replace(output_path)
    write_weather_station_geojson(output_path)
    return output_path


def write_weather_station_geojson(
    mapping_path: Path, output_path: Path | None = None,
) -> Path:
    """Export unique mapped stations as WGS84 points beside the mapping CSV.

    Pending rows without station coordinates are omitted. Hospital IDs and
    observation roles are aggregated without duplicating station features.
    """
    output_path = output_path or mapping_path.with_name("weather_stations.geojson")
    if output_path.resolve() == mapping_path.resolve():
        raise ValueError("Station GeoJSON and mapping CSV must have different paths")
    stations: dict[str, dict[str, Any]] = {}
    with mapping_path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            station_id = row["bom_station_id"].strip()
            if not station_id or not row["station_latitude"] or not row["station_longitude"]:
                continue
            latitude = float(row["station_latitude"])
            longitude = float(row["station_longitude"])
            if not (
                isfinite(latitude) and isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180
            ):
                raise ValueError(f"Invalid coordinates for BoM station {station_id}")
            identity = (row["bom_station_name"], longitude, latitude)
            station = stations.setdefault(station_id, {
                "identity": identity, "hospital_ids": set(), "roles": set(),
            })
            if station["identity"] != identity:
                raise ValueError(f"Conflicting name or coordinates for BoM station {station_id}")
            station["hospital_ids"].add(row["bhi_hospital_id"])
            station["roles"].add(row["weather_observation_group"])
    features = []
    for station_id, station in sorted(stations.items()):
        name, longitude, latitude = station["identity"]
        features.append({
            "type": "Feature", "id": station_id,
            "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
            "properties": {
                "bom_station_id": station_id, "bom_station_name": name,
                "weather_observation_groups": sorted(station["roles"]),
                "bhi_hospital_ids": sorted(station["hospital_ids"]),
                "hospital_count": len(station["hospital_ids"]),
            },
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".tmp", dir=output_path.parent, delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump({"type": "FeatureCollection", "features": features}, temporary, indent=2, allow_nan=False)
            temporary.write("\n")
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path
