import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from transform.bhi.lhd_bhi_ed_lags import SERIES, SOURCES, build_lhd_lags


class LhdBhiLagsTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.reference = self.root / "lhds.csv"
        self.reference.write_text("bhi_lhd_id,lhd_name\nL1,First LHD\nL2,Second LHD\n")
        self.output = self.root / "linking/panel.csv"
        self.fields = ["reporting_level", "entity_id", "reporting_period", "measure_value", *SERIES, "note_symbol", "note_text"]

    def row(self, period, value, **changes):
        row = dict.fromkeys(self.fields, "")
        row.update(reporting_level="LHD", entity_id="L1", reporting_period=period,
                   measure_value=value, reporting_category="Total", measure_name="Attendances", metric="Number")
        row.update(changes)
        return row

    def write_sources(self, rows):
        for filename in SOURCES.values():
            with (self.root / filename).open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=self.fields)
                writer.writeheader()
                writer.writerows(rows)

    def test_calendar_filters_and_numeric_only_measures(self):
        self.write_sources([
            self.row("Oct-Dec 2024", "10", note_text="Interpret with caution"),
            self.row("Apr-Jun 2025", "", note_symbol="*", note_text="Suppressed"),
            self.row("Apr-Jun 2025", "999", reporting_level="Hospital"),
            self.row("Apr-Jun 2025", "888", entity_id="NETWORK"),
        ])
        self.assertEqual(build_lhd_lags(self.root, self.reference, self.output), (6, 3))
        with self.output.open(newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            fields = reader.fieldnames
        self.assertEqual(set(fields), {
            "lhd_name", "year", "quarter", "pressure_index",
            *(f"{source}__total__total__attendances__number" for source in SOURCES),
        })
        self.assertEqual({row["lhd_name"] for row in rows}, {"First LHD", "Second LHD"})
        self.assertEqual([(row["year"], row["quarter"]) for row in rows[:3]],
                         [("2024", "4"), ("2025", "1"), ("2025", "2")])
        column = "overall__total__total__attendances__number"
        self.assertEqual([row[column] for row in rows], ["10", "", "", "", "", ""])
        for row in rows:
            for field, value in row.items():
                if field != "lhd_name" and value:
                    float(value)

    def test_duplicate_rejected_without_overwriting_output(self):
        row = self.row("Jan-Mar 2025", "10")
        self.write_sources([row, row])
        self.output.parent.mkdir()
        self.output.write_text("existing\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_lhd_lags(self.root, self.reference, self.output)
        self.assertEqual(self.output.read_text(), "existing\n")

    def test_measure_subcategories_and_units_remain_distinct(self):
        self.write_sources([
            self.row("Jan-Mar 2025", "10", subcategory="Admitted"),
            self.row("Jan-Mar 2025", "20", subcategory="Discharged"),
            self.row("Jan-Mar 2025", "30", subcategory="Discharged", metric="%"),
        ])
        self.assertEqual(build_lhd_lags(self.root, self.reference, self.output), (2, 9))

    def test_pressure_index_and_missing_measures(self):
        self.write_sources([
            self.row("Jan-Mar 2025", "80", measure_name="Percentage of patients leaving ED within four hours", metric="%"),
            self.row("Jan-Mar 2025", "60", measure_name="Percentage of patients starting treatment on time", metric="%"),
        ])
        build_lhd_lags(self.root, self.reference, self.output)
        with self.output.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertAlmostEqual(float(rows[0]["pressure_index"]), (0.4 * 0.2 + 0.35 * 0.4) / 0.75)
        self.assertEqual(rows[1]["pressure_index"], "")
        weights = self.root / "weights.toml"
        weights.write_text('[[measures]]\ncolumn = "overall__total__total__percentage_of_patients_starting_treatment_on_time__percent"\nweight = 7\n')
        build_lhd_lags(self.root, self.reference, self.output, weights)
        with self.output.open(newline="") as stream:
            self.assertAlmostEqual(float(next(csv.DictReader(stream))["pressure_index"]), 0.4)

    def test_invalid_values(self):
        self.write_sources([self.row("Jan-Mar 2025", "NaN")])
        with self.assertRaisesRegex(ValueError, "Invalid numeric"):
            build_lhd_lags(self.root, self.reference, self.output)


if __name__ == "__main__":
    unittest.main()
