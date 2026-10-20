from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from ingestion.SA1_shapefile import (
    boundary_page, discover_shapefile, ingest_sa1_shapefile,
)
from tests.test_seifa import FakeResponse


STAMP = datetime(2026, 10, 20, tzinfo=timezone.utc)


def archive_bytes(year=2021, missing=None):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for suffix in ("shp", "shx", "dbf", "prj"):
            if suffix != missing:
                archive.writestr(f"SA1_{year}_AUST_GDA2020.{suffix}", b"fixture")
    return output.getvalue()


class SA1ShapefileTests(unittest.TestCase):
    def test_download_partition_and_reuse_without_network(self):
        for year in (2021, 2026):
            with self.subTest(year=year), TemporaryDirectory() as directory:
                page = boundary_page(year)
                url = page + f"/SA1_{year}_AUST_SHP_GDA2020.zip"
                calls = []
                responses = []

                def get(request_url, **kwargs):
                    calls.append(request_url)
                    response = (
                        FakeResponse(text=f'<a href="{url}">SA1</a>')
                        if request_url == page
                        else FakeResponse(body=archive_bytes(year), content_type="application/zip")
                    )
                    responses.append(response)
                    return response

                first = ingest_sa1_shapefile(Path(directory), STAMP, year, get=get)
                again = ingest_sa1_shapefile(Path(directory), STAMP, year, get=get)
                self.assertEqual(first.status, "created")
                self.assertEqual(again.status, "unchanged")
                self.assertEqual(first.path, again.path)
                self.assertEqual(first.sha256, again.sha256)
                self.assertEqual(Path(first.path).parent, Path(directory) / f"abs/SA1/census_year={year}")
                self.assertEqual(calls, [page, url])
                self.assertTrue(all(response.closed for response in responses))

    def test_rejects_wrong_year_datum_and_untrusted_host(self):
        page = boundary_page(2021)
        for filename in (
            "SA1_2026_AUST_SHP_GDA2020.zip", "SA1_2021_AUST_SHP_GDA94.zip",
            "https://example.test/SA1_2021_AUST_SHP_GDA2020.zip",
        ):
            with self.subTest(filename=filename), self.assertRaisesRegex(RuntimeError, "unique"):
                discover_shapefile(f'<a href="{filename}">download</a>', page, 2021)

    def test_invalid_downloads_leave_no_snapshot_or_partial(self):
        page = boundary_page(2021)
        url = page + "/SA1_2021_AUST_SHP_GDA2020.zip"
        for body in (b"<html>error</html>", archive_bytes(missing="dbf"), archive_bytes(2026)):
            with self.subTest(body=body[:10]), TemporaryDirectory() as directory:
                def get(request_url, **kwargs):
                    return (FakeResponse(text=f'<a href="{url}">SA1</a>')
                            if request_url == page else FakeResponse(body=body))

                with self.assertRaises(RuntimeError):
                    ingest_sa1_shapefile(Path(directory), STAMP, 2021, get=get)
                self.assertEqual(list(Path(directory).rglob("*.zip")), [])

    def test_dag_connects_seifa_to_matching_boundary_task(self):
        try:
            from airflow.dag_processing.dagbag import DagBag
        except ImportError:
            self.skipTest("Airflow is not installed")
        dag_file = Path(__file__).resolve().parents[1] / "airflow/dags/ingest_seifa.py"
        bag = DagBag(dag_folder=str(dag_file))
        self.assertEqual(bag.import_errors, {})
        dag = bag.dags["ingest_seifa"]
        self.assertEqual(
            dag.get_task("ingest_if_new_period").downstream_task_ids,
            {"ingest_sa1_shapefile"},
        )


if __name__ == "__main__":
    unittest.main()
