from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ingestion.bom import (
    PUBLIC_STATION_CATALOG_URL,
    ingest_public_observations,
    nth_working_day,
    parse_public_station_catalog,
    parse_public_station_observations,
    reporting_month_for_summary_check,
)


CATALOG_XML = b"""<?xml version="1.0"?>
<product>
  <amoc>
    <identifier>IDN60920</identifier>
    <issue-time-utc>2026-10-18T13:31:04+00:00</issue-time-utc>
  </amoc>
  <observations>
    <station wmo-id="94768" bom-id="066214" />
  </observations>
</product>
"""


def station_json(wmo_id: int = 94768, *, array_header: bool = False) -> bytes:
    header: dict[str, str] | list[dict[str, str]] = {"ID": "IDN60801"}
    if array_header:
        header = [header]
    return json.dumps(
        {
            "observations": {
                "header": header,
                "data": [
                    {"wmo": wmo_id, "aifstime_utc": "20261018130000"},
                    {"wmo": wmo_id, "aifstime_utc": "20261018133000"},
                ],
            }
        }
    ).encode("utf-8")


class FakeResponse:
    def __init__(self, content: bytes, content_type: str) -> None:
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        return None


class BomReleaseWindowTests(unittest.TestCase):
    def test_october_2026_release_working_days(self) -> None:
        self.assertEqual(nth_working_day(2026, 10, 1).isoformat(), "2026-10-01")
        self.assertEqual(nth_working_day(2026, 10, 3).isoformat(), "2026-10-05")

    def test_regional_check_waits_until_third_working_day(self) -> None:
        before = datetime(2026, 10, 5, 3, 59, tzinfo=timezone.utc)
        at_release = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)

        self.assertIsNone(reporting_month_for_summary_check(before))
        reporting_month = reporting_month_for_summary_check(at_release)
        self.assertIsNotNone(reporting_month)
        self.assertEqual(reporting_month.period, "202609")

    def test_national_window_opens_on_first_working_day(self) -> None:
        reporting_month = reporting_month_for_summary_check(
            datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc),
            summary_scope="national",
        )

        self.assertEqual(reporting_month.period, "202609")

    def test_release_window_handles_january_year_boundary(self) -> None:
        reporting_month = reporting_month_for_summary_check(
            datetime(2027, 1, 5, 4, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(reporting_month.period, "202612")

    def test_rejects_naive_check_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            reporting_month_for_summary_check(datetime(2026, 10, 5, 15))


class BomPublicFeedTests(unittest.TestCase):
    def test_parses_catalog_and_station_observations(self) -> None:
        catalog = parse_public_station_catalog(CATALOG_XML)
        station = parse_public_station_observations(
            station_json(array_header=True), "94768"
        )

        self.assertEqual(catalog.wmo_by_bom_id, {"066214": "94768"})
        self.assertEqual(station.observation_count, 2)
        self.assertEqual(
            station.latest_observation_at.isoformat(), "2026-10-18T13:30:00+00:00"
        )

    def test_ingests_supported_stations_and_reports_unsupported_ones(self) -> None:
        def get(url: str, **_: object) -> FakeResponse:
            if url == PUBLIC_STATION_CATALOG_URL:
                return FakeResponse(CATALOG_XML, "text/xml; charset=UTF-8")
            if url.endswith("IDN60801.94768.json"):
                return FakeResponse(station_json(), "application/json")
            raise AssertionError(f"Unexpected URL: {url}")

        mapping_rows = [
            {"bom_station_id": "066214"},
            {"bom_station_id": "012345"},
        ]
        with TemporaryDirectory() as directory:
            raw_root = Path(directory)
            first = ingest_public_observations(
                raw_root,
                datetime(2026, 10, 19, 9, tzinfo=timezone.utc),
                mapping_rows,
                get=get,
                max_workers=1,
            )
            second = ingest_public_observations(
                raw_root,
                datetime(2026, 10, 20, 9, tzinfo=timezone.utc),
                mapping_rows,
                get=get,
                max_workers=1,
            )
            station_files = list(
                raw_root.glob("bom/station_observations/**/*.json")
            )

        self.assertEqual(first.status, "created")
        self.assertEqual(first.created_count, 1)
        self.assertEqual(first.unsupported_station_ids, ("012345",))
        self.assertEqual(second.status, "unchanged")
        self.assertEqual(second.unchanged_count, 1)
        self.assertEqual(len(station_files), 1)

    def test_rejects_station_product_for_another_wmo_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            parse_public_station_observations(station_json(94767), "94768")


if __name__ == "__main__":
    unittest.main()
