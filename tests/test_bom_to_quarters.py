import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from transform.bom.bom_to_quarters import build_quarters


class BomQuarterTests(unittest.TestCase):
    def test_deduplicates_and_groups_using_local_calendar(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            station = root / "raw" / "station_id=066161"
            station.mkdir(parents=True)
            first = {"aifstime_utc": "20260331133000",
                     "local_date_time_full": "20260401003000", "name": "Station",
                     "air_temp": 20, "rain_trace": "2.0"}
            second = {**first, "aifstime_utc": "20260331140000",
                      "local_date_time_full": "20260401010000", "air_temp": 30,
                      "rain_trace": "3.0"}
            previous = {**first, "aifstime_utc": "20260330130000",
                        "local_date_time_full": "20260331000000", "air_temp": None}
            for name, rows in [("snapshot_20260401.json", [first, previous]),
                               ("snapshot_20260402.json", [{**first, "air_temp": 22}, second])]:
                (station / name).write_text(json.dumps({"observations": {"data": rows}}))
            output = root / "silver" / "quarters.csv"
            self.assertEqual(build_quarters(root / "raw", output), 2)
            with output.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["temperature_c_mean"], "")
            self.assertEqual(rows[0]["temperature_c_observation_count"], "0")
            quarter = rows[1]
            self.assertEqual(quarter["bom_station_id"], "066161")
            self.assertEqual(quarter["quarter"], "2")
            self.assertEqual(quarter["observation_count"], "2")
            self.assertEqual(quarter["temperature_c_mean"], "26.0")
            self.assertEqual(quarter["rain_since_9am_max_mm"], "3.0")
            self.assertEqual(quarter["observed_day_count"], "1")
            self.assertEqual(quarter["expected_day_count"], "91")
            self.assertEqual(quarter["incomplete_quarter"], "True")

    def test_empty_input_does_not_replace_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "quarters.csv"
            output.write_text("existing")
            with self.assertRaisesRegex(ValueError, "No station JSON"):
                build_quarters(root, output)
            self.assertEqual(output.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
