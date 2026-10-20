from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ingestion import demo_ingest


class DemoIngestTests(unittest.TestCase):
    def test_explain_only_does_not_contact_sources(self) -> None:
        output = StringIO()
        with (
            patch("ingestion.demo_ingest.ingest_latest_ed") as bhi,
            patch("ingestion.demo_ingest.ingest_public_observations") as bom,
            patch("ingestion.demo_ingest.ingest_sa1_release") as seifa,
            patch("ingestion.demo_ingest.load_approved_weather_mapping") as mapping,
            redirect_stdout(output),
        ):
            status = demo_ingest.main(["--explain-only"])

        self.assertEqual(status, 0)
        bhi.assert_not_called()
        bom.assert_not_called()
        seifa.assert_not_called()
        mapping.assert_not_called()
        self.assertIn("BHI emergency-department performance", output.getvalue())
        self.assertIn("BoM public NSW station observations", output.getvalue())
        self.assertIn("ABS SEIFA at SA1 level", output.getvalue())

    def test_bhi_demo_reports_the_output_path(self) -> None:
        with TemporaryDirectory() as directory:
            raw_root = Path(directory) / "raw"
            stored = raw_root / "bhi" / "ed_performance" / "snapshot.xlsx"
            result = SimpleNamespace(
                status="unchanged",
                reporting_period="2026Q2",
                source_url="https://example.test/bhi.xlsx",
                sha256="abc123",
                path=str(stored),
            )
            output = StringIO()
            with (
                patch("ingestion.demo_ingest.ingest_latest_ed", return_value=result) as ingest,
                redirect_stdout(output),
            ):
                status = demo_ingest.main(
                    ["--source", "bhi", "--raw-root", str(raw_root)]
                )

        self.assertEqual(status, 0)
        ingest.assert_called_once()
        self.assertEqual(ingest.call_args.args[0], raw_root.resolve())
        self.assertIsInstance(ingest.call_args.args[1], datetime)
        self.assertEqual(ingest.call_args.args[1].tzinfo, timezone.utc)
        self.assertIn(str(stored.resolve()), output.getvalue())
        self.assertIn("All selected ingestion checks completed", output.getvalue())

    def test_bom_uses_approved_mapping_and_reports_outputs(self) -> None:
        result = SimpleNamespace(
            status="created", catalog_path="/tmp/catalog.xml",
            station_snapshot_count=2, created_count=1, unchanged_count=1,
            unsupported_station_ids=("999999",),
        )
        rows = [{"bom_station_id": "066062"}]
        output = StringIO()
        with (
            patch("ingestion.demo_ingest.load_approved_weather_mapping", return_value=rows) as mapping,
            patch("ingestion.demo_ingest.ingest_public_observations", return_value=result) as ingest,
            redirect_stdout(output),
        ):
            status = demo_ingest.main(["--source", "bom", "--raw-root", "/tmp/raw",
                                       "--mapping", "/tmp/mapping.csv", "--hospitals", "/tmp/hospitals.csv"])
        self.assertEqual(status, 0)
        mapping.assert_called_once_with(Path("/tmp/mapping.csv"), Path("/tmp/hospitals.csv"))
        self.assertEqual(ingest.call_args.args[2], rows)
        self.assertIn("/tmp/raw/bom/station_observations", output.getvalue())
        self.assertIn("999999", output.getvalue())

    def test_failure_does_not_prevent_remaining_sources(self) -> None:
        result = SimpleNamespace(status="unchanged", reporting_period="2021",
                                 source_url="https://example.test/seifa.xlsx", path="/tmp/seifa.xlsx")
        with (
            patch("ingestion.demo_ingest.ingest_latest_ed", side_effect=RuntimeError("BHI unavailable")),
            patch("ingestion.demo_ingest.load_approved_weather_mapping", side_effect=ValueError("invalid mapping")),
            patch("ingestion.demo_ingest.ingest_sa1_release", return_value=result) as seifa,
            redirect_stdout(StringIO()),
        ):
            status = demo_ingest.main([])
        self.assertEqual(status, 1)
        seifa.assert_called_once()

    def test_source_failure_is_reported_and_returns_nonzero(self) -> None:
        output = StringIO()
        with (
            patch(
                "ingestion.demo_ingest.ingest_sa1_release",
                side_effect=RuntimeError("release unavailable"),
            ),
            redirect_stdout(output),
        ):
            status = demo_ingest.main(["--source", "seifa"])

        self.assertEqual(status, 1)
        self.assertIn("[ABS SEIFA] FAILED", output.getvalue())
        self.assertIn("Failed sources: ABS SEIFA", output.getvalue())


if __name__ == "__main__":
    unittest.main()
