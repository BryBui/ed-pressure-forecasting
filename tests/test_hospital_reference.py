import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ingestion.bom import (
    WEATHER_FEATURES,
    load_approved_weather_mapping,
    unresolved_feature_decisions,
)
from reference.extract_hospitals import extract_hospitals
from reference.extract_lhds import extract_geographic_lhds
from reference.map_hospitals_to_weather import (
    MAPPING_FIELDS,
    MYHOSPITALS_QUERY_URL,
    STATION_ROLES,
    BomStation,
    fetch_official_hospital_locations,
    parse_bom_station_list,
    select_hospital_station_mappings,
    write_weather_station_geojson,
)
from reference.validate_references import BOUNDARY_QUERY_URL, build_reference_data
from tests import FakeResponse, boundary_geojson, make_workbook


def station_catalogs() -> dict[str, list[BomStation]]:
    station = BomStation(
        station_id="066137",
        station_name="TEST AIRPORT AWS",
        latitude=-33.8,
        longitude=151.1,
        record_start="1980-01",
        record_end="2026-10",
        record_years=46.0,
        completeness_pct=99.0,
    )
    return {
        code: [station]
        for role in STATION_ROLES
        for code in role.element_codes
    }


class HospitalReferenceTests(unittest.TestCase):
    def test_flags_role_specific_distance_outliers(self) -> None:
        catalogs = station_catalogs()
        catalogs["12"] = [
            BomStation(
                station_id="066999",
                station_name="DISTANT VISUAL STATION",
                latitude=-34.8,
                longitude=151.1,
                record_start="1980-01",
                record_end="2026-10",
                record_years=46.0,
                completeness_pct=99.0,
            )
        ]

        proposals = select_hospital_station_mappings(
            [{"bhi_hospital_id": "H001"}],
            {
                "H001": {
                    "hospital_latitude": "-33.8",
                    "hospital_longitude": "151.1",
                }
            },
            catalogs,
        )

        self.assertEqual(proposals[("H001", "temperature")]["mapping_status"], "approved")
        self.assertEqual(
            proposals[("H001", "temperature")]["mapping_quality"],
            "preferred_distance",
        )
        self.assertEqual(
            proposals[("H001", "thunderstorms")]["mapping_status"],
            "approved",
        )
        self.assertEqual(
            proposals[("H001", "thunderstorms")]["mapping_quality"],
            "regional_proxy",
        )

    def test_parses_bom_element_station_list(self) -> None:
        text = """Bureau of Meteorology product IDCJMC0015.
Site    Name                                     Lat       Lon      Start    End       Years   %      Obs AWS
  66137 BANKSTOWN AIRPORT AWS                    -33.9176  150.9837 Jul 1968 Oct 2026   58.2   99     9.6   Y
"""

        stations = parse_bom_station_list(text)

        self.assertEqual(stations[0].station_id, "066137")
        self.assertEqual(stations[0].record_end, "2026-10")
        self.assertEqual(stations[0].completeness_pct, 99.0)

    def test_matches_reviewed_hospital_location_aliases(self) -> None:
        hospitals = [
            {
                "bhi_hospital_id": "A212",
                "hospital_name": "St Vincent's Hospital Sydney",
            },
            {
                "bhi_hospital_id": "H223",
                "hospital_name": "Tweed Valley Hospital",
            },
        ]

        def get(url, **kwargs):
            if url == MYHOSPITALS_QUERY_URL:
                return FakeResponse(
                    {
                        "features": [
                            {
                                "attributes": {
                                    "hospital_code": "H0017",
                                    "hospital_name": "St Vincent's Hospital [Darlinghurst]",
                                    "latitude": -33.879692,
                                    "longitude": 151.221125,
                                }
                            }
                        ]
                    }
                )
            return FakeResponse(
                {
                    "features": [
                        {
                            "attributes": {
                                "topoid": 505469665,
                                "generalname": "THE TWEED VALLEY HOSPITAL",
                            },
                            "geometry": {"x": 153.566226, "y": -28.264013},
                        }
                    ]
                }
            )

        locations = fetch_official_hospital_locations(hospitals, get)

        self.assertEqual(locations["A212"]["hospital_location_id"], "H0017")
        self.assertEqual(
            locations["H223"]["hospital_location_source"],
            "NSW Health Facilities",
        )

    def test_preserves_bhi_ids_relationships_and_history(self) -> None:
        with TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "source.xlsx"
            make_workbook(workbook_path)
            lhds = extract_geographic_lhds(workbook_path)

            hospitals = extract_hospitals(workbook_path, lhds)

        current = next(row for row in hospitals if row.bhi_hospital_id == "H001")
        network = next(row for row in hospitals if row.bhi_hospital_id == "H002")
        historical = next(row for row in hospitals if row.bhi_hospital_id == "H003")
        self.assertEqual(current.geographic_lhd_id, "CCLHD")
        self.assertEqual(network.bhi_parent_type, "specialty_network")
        self.assertEqual(network.geographic_lhd_id, "")
        self.assertEqual(historical.present_in_latest_quarter, "false")

    def test_builds_hospital_reference_mapping_and_validation_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = (
                root
                / "raw/bhi/ed_performance/year=2026/quarter=Q2"
                / "ed_performance_2026Q2_20261018T090000Z.xlsx"
            )
            workbook_path.parent.mkdir(parents=True)
            make_workbook(workbook_path)

            result = build_reference_data(
                root / "raw",
                root / "reference",
                get=lambda url, **kwargs: FakeResponse(
                    boundary_geojson()
                    if url == BOUNDARY_QUERY_URL
                    else {
                        "features": [
                            {
                                "attributes": {
                                    "hospital_code": "TEST001",
                                    "hospital_name": "Coastal Hospital",
                                    "latitude": -33.4,
                                    "longitude": 151.3,
                                }
                            },
                            {
                                "attributes": {
                                    "hospital_code": "TEST002",
                                    "hospital_name": "Children's Network Hospital",
                                    "latitude": -33.9,
                                    "longitude": 151.2,
                                }
                            },
                        ]
                    }
                ),
                station_catalogs=station_catalogs(),
            )

            self.assertEqual(result["hospital_count"], 2)
            with (root / "reference/nsw_hospitals.csv").open(
                encoding="utf-8", newline=""
            ) as source:
                hospitals = list(csv.DictReader(source))
            with (root / "reference/hospital_weather_mapping.csv").open(
                encoding="utf-8", newline=""
            ) as source:
                mappings = list(csv.DictReader(source))
            report = json.loads(
                (root / "reference/reference_validation_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(hospitals), 2)
            self.assertEqual(len(mappings), 10)
            station_points = json.loads(
                (root / "reference/weather_stations.geojson").read_text()
            )
            self.assertEqual(station_points["type"], "FeatureCollection")
            self.assertEqual(len(station_points["features"]), 1)
            point = station_points["features"][0]
            self.assertEqual(point["properties"]["bom_station_id"], "066137")
            self.assertEqual(point["geometry"]["coordinates"], [151.1, -33.8])
            self.assertEqual(point["properties"]["hospital_count"], 2)
            self.assertEqual(
                point["properties"]["weather_observation_groups"],
                sorted(role.name for role in STATION_ROLES),
            )
            self.assertEqual(
                {row["mapping_status"] for row in mappings}, {"approved"}
            )
            self.assertTrue(all(row["hospital_latitude"] for row in mappings))
            self.assertEqual(
                {row["weather_observation_group"] for row in mappings},
                {role.name for role in STATION_ROLES},
            )
            self.assertFalse(report["authoritative_hospital_register_checked"])
            self.assertEqual(report["source_unique_hospital_count"], 3)
            self.assertEqual(report["excluded_historical_only_hospital_count"], 1)
            approved = load_approved_weather_mapping(
                root / "reference/hospital_weather_mapping.csv",
                root / "reference/nsw_hospitals.csv",
            )
            self.assertEqual(len(approved), 10)

    def test_station_geojson_handles_pending_rows_and_rejects_conflicts(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            base = dict.fromkeys(MAPPING_FIELDS, "")
            base.update(
                bhi_hospital_id="H001", weather_observation_group="rainfall",
                bom_station_id="066137", bom_station_name="Test",
                station_latitude="-33.8", station_longitude="151.1",
            )

            def write(rows):
                with path.open("w", newline="") as source:
                    writer = csv.DictWriter(source, fieldnames=MAPPING_FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)

            write([base, {**base, "bhi_hospital_id": "H002"}, dict.fromkeys(MAPPING_FIELDS, "")])
            output = write_weather_station_geojson(path)
            payload = json.loads(output.read_text())
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["properties"]["bhi_hospital_ids"], ["H001", "H002"])
            original = output.read_bytes()
            write([base, {**base, "station_latitude": "-34"}])
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                write_weather_station_geojson(path)
            self.assertEqual(output.read_bytes(), original)
            write([{**base, "station_latitude": "nan"}])
            with self.assertRaisesRegex(ValueError, "Invalid coordinates"):
                write_weather_station_geojson(path)
            write([dict.fromkeys(MAPPING_FIELDS, "")])
            write_weather_station_geojson(path)
            self.assertEqual(json.loads(output.read_text())["features"], [])

    def test_approved_mapping_must_cover_hospital_reference(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hospital_path = root / "nsw_hospitals.csv"
            mapping_path = root / "hospital_weather_mapping.csv"
            with hospital_path.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=["bhi_hospital_id"])
                writer.writeheader()
                writer.writerow({"bhi_hospital_id": "H001"})
            with mapping_path.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=MAPPING_FIELDS)
                writer.writeheader()
                for role in STATION_ROLES:
                    row = {field: "" for field in MAPPING_FIELDS}
                    row.update(
                        bhi_hospital_id="H001",
                        hospital_name="Hospital",
                        bhi_parent_name="Central Coast",
                        geographic_lhd_id="CCLHD",
                        hospital_latitude="-33.0",
                        hospital_longitude="151.0",
                        weather_observation_group=role.name,
                        bom_element_codes="+".join(role.element_codes),
                        bom_station_id="061234",
                        bom_station_name="Station",
                        station_latitude="-33.1",
                        station_longitude="151.1",
                        distance_km="5.0",
                        mapping_method=(
                            "nearest_active_station_with_required_elements"
                        ),
                        mapping_status="approved",
                    )
                    writer.writerow(row)

            rows = load_approved_weather_mapping(mapping_path, hospital_path)

        self.assertEqual(len(rows), 5)

    def test_weather_feature_plan_uses_daily_inputs(self) -> None:
        groups = {feature.feature_group: feature for feature in WEATHER_FEATURES}

        self.assertEqual(len(groups), 9)
        self.assertIn("maximum_temperature_c", groups["heatwaves"].required_daily_fields)
        self.assertIn("thunder_observed", groups["thunderstorms"].required_daily_fields)
        self.assertEqual(groups["extreme_heat_days"].threshold_value, 35.0)
        self.assertEqual(groups["cold_days"].threshold_field, "maximum_temperature_c")
        self.assertEqual(groups["cold_days"].threshold_value, 10.0)
        self.assertEqual(groups["cold_nights"].threshold_field, "minimum_temperature_c")
        self.assertEqual(groups["cold_nights"].threshold_value, 5.0)
        self.assertEqual(unresolved_feature_decisions(), ())


if __name__ == "__main__":
    unittest.main()
