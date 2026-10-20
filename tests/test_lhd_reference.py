from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from reference.extract_lhds import extract_geographic_lhds
from reference.validate_references import validate_lhds
from tests import boundary_geojson, make_workbook


class LhdReferenceTests(unittest.TestCase):
    def test_extracts_only_the_15_geographic_lhds(self) -> None:
        with TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "source.xlsx"
            make_workbook(workbook_path)

            records = extract_geographic_lhds(workbook_path)

        self.assertEqual(len(records), 15)
        self.assertNotIn("SCHN", {record.bhi_lhd_id for record in records})
        self.assertNotIn("SVHN", {record.bhi_lhd_id for record in records})

    def test_validates_lhds_against_official_boundaries(self) -> None:
        with TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "source.xlsx"
            make_workbook(workbook_path)
            records = extract_geographic_lhds(workbook_path)

            validated = validate_lhds(records, boundary_geojson())

        self.assertEqual(len(validated), 15)
        sydney = next(
            row for row in validated if row["bhi_lhd_id"] == "SYDLHD"
        )
        self.assertEqual(sydney["boundary_code"], "X700")


if __name__ == "__main__":
    unittest.main()
