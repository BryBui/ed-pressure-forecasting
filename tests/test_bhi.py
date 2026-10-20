from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ingestion.bhi import discover_ed_workbook, ingest_latest_ed, workbook_from_url


WORKBOOK_URL = (
    "https://example.test/data/BHI_DATA_HQ_ED_Jan2010Sep2026.xlsx"
)
HTML = f'<a class="box-link-emergency-department" href="{WORKBOOK_URL}">ED</a>'
XLSX_BYTES = b"PK\x03\x04fake-workbook"


class FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        body: bytes = b"",
        content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) -> None:
        self.text = text
        self.body = body
        self.headers = {"content-type": content_type}
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body

    def close(self) -> None:
        self.closed = True


def fake_get(url: str, **kwargs):
    del kwargs
    if url.endswith("downloadable-data"):
        return FakeResponse(text=HTML, content_type="text/html")
    if url == WORKBOOK_URL:
        return FakeResponse(body=XLSX_BYTES)
    raise AssertionError(f"Unexpected URL: {url}")


class BhiTests(unittest.TestCase):
    def test_parses_workbook_reporting_quarter(self) -> None:
        workbook = workbook_from_url(WORKBOOK_URL)

        self.assertIsNotNone(workbook)
        self.assertEqual(workbook.reporting_period, "2026Q3")

    def test_discovers_ed_link_from_dataset_class(self) -> None:
        workbook = discover_ed_workbook(HTML)

        self.assertEqual(workbook.url, WORKBOOK_URL)

    def test_ingestion_is_partitioned_and_duplicate_safe(self) -> None:
        with TemporaryDirectory() as directory:
            raw_root = Path(directory)
            first = ingest_latest_ed(
                raw_root,
                datetime(2026, 11, 15, 9, tzinfo=timezone.utc),
                get=fake_get,
            )
            retry = ingest_latest_ed(
                raw_root,
                datetime(2026, 11, 15, 9, tzinfo=timezone.utc),
                get=fake_get,
            )
            later_poll = ingest_latest_ed(
                raw_root,
                datetime(2026, 12, 5, 9, tzinfo=timezone.utc),
                get=fake_get,
            )

            expected = (
                raw_root
                / "bhi/ed_performance/year=2026/quarter=Q3"
                / "ed_performance_2026Q3_20261115T090000Z.xlsx"
            )
            self.assertEqual(first.status, "created")
            self.assertEqual(Path(first.path), expected)
            self.assertEqual(retry.status, "unchanged")
            self.assertEqual(later_poll.status, "unchanged")
            self.assertEqual(Path(later_poll.path), expected)
            self.assertEqual(list(expected.parent.glob("*.xlsx")), [expected])

    def test_revised_workbook_creates_a_new_snapshot(self) -> None:
        revision = {"body": XLSX_BYTES}

        def revision_get(url: str, **kwargs):
            del kwargs
            if url.endswith("downloadable-data"):
                return FakeResponse(text=HTML, content_type="text/html")
            return FakeResponse(body=revision["body"])

        with TemporaryDirectory() as directory:
            raw_root = Path(directory)
            first = ingest_latest_ed(
                raw_root,
                datetime(2026, 11, 15, 9, tzinfo=timezone.utc),
                get=revision_get,
            )
            revision["body"] = b"PK\x03\x04revised-workbook"
            revised = ingest_latest_ed(
                raw_root,
                datetime(2026, 11, 22, 9, tzinfo=timezone.utc),
                get=revision_get,
            )

            self.assertEqual(first.status, "created")
            self.assertEqual(revised.status, "created")
            self.assertNotEqual(first.path, revised.path)
            self.assertEqual(
                len(list(Path(first.path).parent.glob("*.xlsx"))), 2
            )


if __name__ == "__main__":
    unittest.main()
