"""BoM public-feed ingestion and weather-mapping validation."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from ingestion.storage import build_snapshot_path, format_ingestion_timestamp
from reference.map_hospitals_to_weather import MAPPING_FIELDS, STATION_ROLE_NAMES


SYDNEY_TIME = ZoneInfo("Australia/Sydney")
SUMMARY_RELEASE_WORKING_DAY = {"national": 1, "regional": 3}
SUMMARY_CHECK_TIME = time(15, 0)
PUBLIC_STATION_CATALOG_URL = "https://www.bom.gov.au/fwo/IDN60920.xml"
PUBLIC_STATION_PRODUCT = "IDN60801"
PUBLIC_STATION_URL = (
    "https://www.bom.gov.au/fwo/IDN60801/IDN60801.{wmo_id}.json"
)


@dataclass(frozen=True)
class PublicStationCatalog:
    """Station identifiers advertised by the NSW public observation feed."""

    issued_at: datetime
    wmo_by_bom_id: dict[str, str]


@dataclass(frozen=True)
class PublicStationObservations:
    """Validated metadata from one 72-hour station JSON product."""

    wmo_id: str
    latest_observation_at: datetime
    observation_count: int


@dataclass(frozen=True)
class PublicObservationIngestionResult:
    """Serializable outcome from one public-feed collection run."""

    status: str
    catalog_path: str
    station_snapshot_count: int
    created_count: int
    unchanged_count: int
    supported_station_count: int
    unsupported_station_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["unsupported_station_ids"] = list(self.unsupported_station_ids)
        return result


@dataclass(frozen=True)
class ReportingMonth:
    """A completed calendar month eligible for a BoM release check."""

    year: int
    month: int

    @property
    def period(self) -> str:
        return f"{self.year:04d}{self.month:02d}"


def nth_working_day(year: int, month: int, ordinal: int) -> date:
    """Return the nth Monday-to-Friday day in a calendar month."""
    if ordinal < 1:
        raise ValueError("ordinal must be at least 1")
    candidate = date(year, month, 1)
    count = 0
    while True:
        if candidate.weekday() < 5:
            count += 1
            if count == ordinal:
                return candidate
        candidate += timedelta(days=1)


def reporting_month_for_summary_check(
    checked_at: datetime, *, summary_scope: str = "regional"
) -> ReportingMonth | None:
    """Return the prior month once its BoM summary release window has opened.

    The calendar is only an earliest-check gate. Callers must still verify that
    the station files contain the complete month and compare their content with
    existing snapshots before publishing a new raw version.
    """
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("checked_at must be timezone-aware")
    try:
        release_ordinal = SUMMARY_RELEASE_WORKING_DAY[summary_scope]
    except KeyError as error:
        raise ValueError(
            f"summary_scope must be one of {sorted(SUMMARY_RELEASE_WORKING_DAY)}"
        ) from error

    local_check = checked_at.astimezone(SYDNEY_TIME)
    release_date = nth_working_day(
        local_check.year, local_check.month, release_ordinal
    )
    release_at = datetime.combine(release_date, SUMMARY_CHECK_TIME, SYDNEY_TIME)
    if local_check < release_at:
        return None

    first_of_month = local_check.date().replace(day=1)
    prior_month = first_of_month - timedelta(days=1)
    return ReportingMonth(prior_month.year, prior_month.month)


def _parse_aware_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid {label}: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed


def parse_public_station_catalog(payload: bytes) -> PublicStationCatalog:
    """Parse the official NSW observation feed used as the station catalogue."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise ValueError("BoM station catalogue is not valid XML") from error
    identifier = root.findtext("./amoc/identifier")
    if identifier != "IDN60920":
        raise ValueError(f"Unexpected BoM station catalogue product: {identifier!r}")
    issue_time = root.findtext("./amoc/issue-time-utc")
    if not issue_time:
        raise ValueError("BoM station catalogue has no UTC issue time")

    stations: dict[str, str] = {}
    for station in root.findall("./observations/station"):
        bom_id = station.get("bom-id", "").strip()
        wmo_id = station.get("wmo-id", "").strip()
        if bom_id and wmo_id:
            stations[bom_id] = wmo_id
    if not stations:
        raise ValueError("BoM station catalogue contains no station identifiers")
    return PublicStationCatalog(
        issued_at=_parse_aware_datetime(issue_time, "catalogue issue time"),
        wmo_by_bom_id=stations,
    )


def parse_public_station_observations(
    payload: bytes, expected_wmo_id: str
) -> PublicStationObservations:
    """Validate one public 72-hour JSON station product."""
    try:
        document = json.loads(payload)
        observations = document["observations"]
        header = observations["header"]
        rows = observations["data"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("BoM station response is not a valid observation product") from error
    if isinstance(header, list) and len(header) == 1:
        header = header[0]
    if not isinstance(header, dict):
        raise ValueError("BoM station response has an invalid product header")
    if header.get("ID") != PUBLIC_STATION_PRODUCT:
        raise ValueError(f"Unexpected BoM station product: {header.get('ID')!r}")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"BoM station {expected_wmo_id} contains no observations")

    timestamps: list[datetime] = []
    for row in rows:
        if str(row.get("wmo", "")) != expected_wmo_id:
            raise ValueError(
                f"BoM station product does not match WMO ID {expected_wmo_id}"
            )
        value = str(row.get("aifstime_utc", ""))
        try:
            timestamps.append(datetime.strptime(value, "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("UTC")
            ))
        except ValueError as error:
            raise ValueError(
                f"BoM station {expected_wmo_id} has an invalid observation time"
            ) from error
    return PublicStationObservations(
        wmo_id=expected_wmo_id,
        latest_observation_at=max(timestamps),
        observation_count=len(rows),
    )


def _default_get(url: str, **kwargs: Any) -> Any:
    try:
        from curl_cffi import requests
    except ImportError as error:
        raise RuntimeError(
            "curl-cffi is required for live BoM downloads; install the project "
            "dependencies first"
        ) from error
    return requests.get(url, impersonate="chrome", **kwargs)


def _fetch_payload(
    url: str,
    expected_content_types: set[str],
    get: Callable[..., Any],
) -> bytes:
    response = get(url, timeout=60)
    try:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in expected_content_types:
            raise RuntimeError(
                f"Expected {sorted(expected_content_types)} from {url}, received "
                f"{content_type or 'no content type'}"
            )
        return bytes(response.content)
    finally:
        response.close()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store_immutable_snapshot(
    destination: Path, payload: bytes
) -> tuple[str, Path]:
    """Atomically store bytes, returning unchanged for identical snapshots."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    checksum = sha256(payload).hexdigest()
    for candidate in sorted(destination.parent.glob(f"*.{destination.suffix.lstrip('.')}")):
        if _sha256_file(candidate) == checksum:
            return "unchanged", candidate

    temporary = NamedTemporaryFile(
        mode="wb",
        suffix=f"{destination.suffix}.part",
        prefix="bom_",
        dir=destination.parent,
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        temporary.write(payload)
        temporary.close()
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if _sha256_file(destination) != checksum:
                raise RuntimeError(
                    f"Snapshot path already exists with different content: {destination}"
                )
            return "unchanged", destination
        return "created", destination
    finally:
        temporary.close()
        temporary_path.unlink(missing_ok=True)


def ingest_public_observations(
    raw_root: Path,
    ingested_at: datetime,
    mapping_rows: Iterable[dict[str, str]],
    *,
    get: Callable[..., Any] = _default_get,
    max_workers: int = 4,
) -> PublicObservationIngestionResult:
    """Collect sanctioned NSW public-feed data for mapped BoM stations.

    The station products contain a rolling 72-hour window. Daily collection
    therefore overlaps observations deliberately, allowing downstream code to
    deduplicate on station and ``aifstime_utc`` without losing a short outage.
    """
    catalog_payload = _fetch_payload(
        PUBLIC_STATION_CATALOG_URL, {"application/xml", "text/xml"}, get
    )
    catalog = parse_public_station_catalog(catalog_payload)
    catalog_local = catalog.issued_at.astimezone(SYDNEY_TIME)
    catalog_path = build_snapshot_path(
        raw_root,
        source="bom",
        dataset="nsw_observation_catalog",
        partitions={
            "year": f"{catalog_local.year:04d}",
            "month": f"{catalog_local.month:02d}",
        },
        reporting_period=format_ingestion_timestamp(catalog.issued_at),
        ingested_at=ingested_at,
        extension="xml",
    )
    catalog_status, stored_catalog_path = _store_immutable_snapshot(
        catalog_path, catalog_payload
    )

    mapped_station_ids = sorted(
        {row["bom_station_id"] for row in mapping_rows if row.get("bom_station_id")}
    )
    supported = {
        station_id: catalog.wmo_by_bom_id[station_id]
        for station_id in mapped_station_ids
        if station_id in catalog.wmo_by_bom_id
    }
    unsupported = tuple(sorted(set(mapped_station_ids) - set(supported)))

    def ingest_station(item: tuple[str, str]) -> tuple[str, str]:
        station_id, wmo_id = item
        url = PUBLIC_STATION_URL.format(wmo_id=wmo_id)
        payload = _fetch_payload(url, {"application/json"}, get)
        metadata = parse_public_station_observations(payload, wmo_id)
        local_observation = metadata.latest_observation_at.astimezone(SYDNEY_TIME)
        destination = build_snapshot_path(
            raw_root,
            source="bom",
            dataset="station_observations",
            partitions={
                "year": f"{local_observation.year:04d}",
                "month": f"{local_observation.month:02d}",
                "station_id": station_id,
            },
            reporting_period=format_ingestion_timestamp(
                metadata.latest_observation_at
            ),
            ingested_at=ingested_at,
            extension="json",
        )
        status, path = _store_immutable_snapshot(destination, payload)
        return status, str(path)

    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        station_results = list(executor.map(ingest_station, supported.items()))

    created_count = sum(status == "created" for status, _ in station_results)
    unchanged_count = sum(status == "unchanged" for status, _ in station_results)
    overall_status = (
        "created"
        if catalog_status == "created" or created_count
        else "unchanged"
    )
    return PublicObservationIngestionResult(
        status=overall_status,
        catalog_path=str(stored_catalog_path),
        station_snapshot_count=len(station_results),
        created_count=created_count,
        unchanged_count=unchanged_count,
        supported_station_count=len(supported),
        unsupported_station_ids=unsupported,
    )


@dataclass(frozen=True)
class WeatherFeatureDefinition:
    """Contract for a monthly Silver-layer weather feature group."""

    feature_group: str
    required_daily_fields: tuple[str, ...]
    monthly_outputs: tuple[str, ...]
    method: str
    status: str = "ready"
    threshold_field: str | None = None
    threshold_operator: str | None = None
    threshold_value: float | None = None


WEATHER_FEATURES = (
    WeatherFeatureDefinition(
        "temperature",
        ("minimum_temperature_c", "maximum_temperature_c"),
        ("temperature_min_c", "temperature_mean_c", "temperature_max_c"),
        "Summarise daily minimum, derived mean, and maximum temperature.",
    ),
    WeatherFeatureDefinition(
        "extreme_heat_days",
        ("maximum_temperature_c",),
        ("extreme_heat_day_count",),
        "Count days where daily maximum temperature is at least 35 degrees Celsius.",
        threshold_field="maximum_temperature_c",
        threshold_operator=">=",
        threshold_value=35.0,
    ),
    WeatherFeatureDefinition(
        "heatwaves",
        ("minimum_temperature_c", "maximum_temperature_c"),
        (
            "heatwave_day_count",
            "heatwave_event_count",
            "heatwave_duration_min_days",
            "heatwave_duration_mean_days",
            "heatwave_duration_max_days",
        ),
        "Use BoM Excess Heat Factor with local climatology and the prior 30 days.",
        "requires_climatology",
    ),
    WeatherFeatureDefinition(
        "cold_days",
        ("maximum_temperature_c",),
        ("cold_day_count",),
        "Count days where daily maximum temperature is at most 10 degrees Celsius.",
        threshold_field="maximum_temperature_c",
        threshold_operator="<=",
        threshold_value=10.0,
    ),
    WeatherFeatureDefinition(
        "cold_nights",
        ("minimum_temperature_c",),
        ("cold_night_count",),
        "Count nights where daily minimum temperature is at most 5 degrees Celsius.",
        threshold_field="minimum_temperature_c",
        threshold_operator="<=",
        threshold_value=5.0,
    ),
    WeatherFeatureDefinition(
        "rainfall",
        ("rainfall_mm",),
        (
            "rainfall_daily_min_mm",
            "rainfall_daily_mean_mm",
            "rainfall_daily_max_mm",
            "rainfall_total_mm",
            "rain_day_count",
        ),
        "Summarise daily rainfall and count days with measurable rain.",
    ),
    WeatherFeatureDefinition(
        "relative_humidity",
        ("relative_humidity_9am_pct", "relative_humidity_3pm_pct"),
        (
            "relative_humidity_min_pct",
            "relative_humidity_mean_pct",
            "relative_humidity_max_pct",
        ),
        "Summarise all available daily 9am and 3pm humidity observations.",
    ),
    WeatherFeatureDefinition(
        "wind",
        ("wind_speed_9am_kmh", "wind_speed_3pm_kmh", "maximum_wind_gust_kmh"),
        (
            "wind_speed_min_kmh",
            "wind_speed_mean_kmh",
            "wind_speed_max_kmh",
            "wind_gust_min_kmh",
            "wind_gust_mean_kmh",
            "wind_gust_max_kmh",
        ),
        "Summarise routine wind speeds and daily maximum gust separately.",
    ),
    WeatherFeatureDefinition(
        "thunderstorms",
        ("thunder_observed",),
        ("thunderstorm_day_count",),
        "Count days on which the station recorded thunder.",
        "station_coverage_dependent",
    ),
)


def unresolved_feature_decisions() -> tuple[str, ...]:
    """Return feature groups that still require a modelling decision."""
    return tuple(
        feature.feature_group
        for feature in WEATHER_FEATURES
        if feature.status == "requires_threshold"
    )


def load_approved_weather_mapping(
    path: Path, hospitals_path: Path | None = None
) -> list[dict[str, str]]:
    """Load mappings only when all BHI hospitals have approved stations."""
    if not path.exists():
        raise FileNotFoundError(f"Hospital weather mapping does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != MAPPING_FIELDS:
            raise ValueError(f"{path.name} must have columns {MAPPING_FIELDS}")
        rows = list(reader)

    pending = sorted(
        {
            f"{row['bhi_hospital_id']}/{row['weather_observation_group']}"
            for row in rows
            if row["mapping_status"] != "approved"
            or not row["bom_station_id"]
            or not row["hospital_latitude"]
            or not row["hospital_longitude"]
            or not row["station_latitude"]
            or not row["station_longitude"]
        }
    )
    if pending:
        raise ValueError(
            "BoM ingestion is blocked until hospital mappings are approved for: "
            + ", ".join(pending)
        )
    mapped_keys = [
        (row["bhi_hospital_id"], row["weather_observation_group"])
        for row in rows
    ]
    if len(set(mapped_keys)) != len(mapped_keys):
        raise ValueError("Approved mapping contains duplicate hospital/role rows")
    invalid_roles = sorted(
        {role for _, role in mapped_keys if role not in STATION_ROLE_NAMES}
    )
    if invalid_roles:
        raise ValueError(f"Approved mapping contains invalid roles: {invalid_roles}")
    if hospitals_path is not None:
        with hospitals_path.open("r", encoding="utf-8-sig", newline="") as source:
            expected_ids = {
                row["bhi_hospital_id"] for row in csv.DictReader(source)
            }
        expected_keys = {
            (hospital_id, role)
            for hospital_id in expected_ids
            for role in STATION_ROLE_NAMES
        }
        if set(mapped_keys) != expected_keys:
            missing = sorted(expected_keys - set(mapped_keys))
            extra = sorted(set(mapped_keys) - expected_keys)
            raise ValueError(
                f"Hospital mapping coverage mismatch; missing={missing}, extra={extra}"
            )
    return rows
