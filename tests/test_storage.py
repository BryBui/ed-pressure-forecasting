from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from ingestion.storage import build_snapshot_path, format_ingestion_timestamp


class StorageTests(unittest.TestCase):
    def test_builds_expected_monthly_path(self) -> None:
        path = build_snapshot_path(
            Path("data/raw"),
            source="bom",
            dataset="monthly_weather_summary",
            partitions={"year": "2026", "month": "09"},
            reporting_period="202609",
            ingested_at=datetime(2026, 10, 5, 20, tzinfo=timezone(timedelta(hours=11))),
            extension="json",
            filename_dataset="weather",
        )

        self.assertEqual(
            path,
            Path("data/raw/bom/monthly_weather_summary/year=2026/month=09")
            / "weather_202609_20261005T090000Z.json",
        )

    def test_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            format_ingestion_timestamp(datetime(2026, 10, 5, 9))

    def test_rejects_unsafe_path_component(self) -> None:
        with self.assertRaises(ValueError):
            build_snapshot_path(
                Path("data/raw"),
                source="../escape",
                dataset="weather",
                partitions={},
                reporting_period="202609",
                ingested_at=datetime.now(timezone.utc),
                extension="json",
            )


if __name__ == "__main__":
    unittest.main()
