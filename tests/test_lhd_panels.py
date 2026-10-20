import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from transform.panels.LHD_panels import build_panels, load_lags


class LHDPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('ref', ['bhi_lhd_id', 'lhd_name', 'boundary_code'], [['A', 'Alpha', 'X1']])
        self.write('bhi', ['lhd_name', 'year', 'quarter', 'pressure_index'],
                   [['Alpha', 2025, 4, .2], ['Alpha', 2026, 2, .4]])
        self.write('socio', ['lhd_name', 'lhd_code', 'census_year', 'irsd_mean'],
                   [['Alpha', 'X1', 2021, 1000]])
        self.write('weather', ['bom_station_id', 'station_name', 'year', 'quarter',
                               'temperature_c_mean', 'incomplete_quarter'],
                   [['001234', 'Station', 2026, 3, 20, True]])
        self.write('map', ['geographic_lhd_id', 'bom_station_id', 'mapping_status'],
                   [['A', '001234', 'approved'], ['A', '001234', 'approved']])

    def write(self, name, fields, rows):
        with (self.root / name).open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)

    def build(self):
        return build_panels(*(self.root / p for p in ['bhi', 'socio', 'weather', 'map', 'ref']),
                            self.root / 'out', (1, 2))

    def test_calendar_lags_missing_values_and_station_deduplication(self):
        self.assertEqual(self.build(), {'A.csv': 4})
        with (self.root / 'out/A.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[1]['pressure_index'], '')  # Missing calendar quarter.
        self.assertEqual(rows[2]['pressure_index__lag_1'], '')
        self.assertEqual(rows[2]['pressure_index__lag_2'], '0.2')
        self.assertEqual(rows[0]['pressure_index__lag_1'], '')
        self.assertEqual(rows[3]['weather__001234__temperature_c_mean'], '20')
        self.assertEqual(rows[2]['weather__001234__temperature_c_mean'], '')
        self.assertEqual(rows[3]['weather__001234__incomplete_quarter'], '1')
        self.assertTrue(all(r['socioeconomic__irsd_mean'] == '1000' for r in rows))

    def test_duplicate_quarters_rejected_before_output(self):
        with (self.root / 'bhi').open('a') as stream:
            stream.write('Alpha,2026,2,0.5\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate BHI'):
            self.build()
        self.assertFalse((self.root / 'out').exists())

    def test_missing_socioeconomic_match_rejected(self):
        self.write('socio', ['lhd_name', 'lhd_code'], [['Other', 'X1']])
        with self.assertRaisesRegex(ValueError, 'LHD names must match'):
            self.build()

    def test_duplicate_weather_rejected(self):
        with (self.root / 'weather').open('a') as stream:
            stream.write('001234,Station,2026,3,21,True\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate station'):
            self.build()

    def test_config_controls_lags_and_rejects_invalid_offsets(self):
        config = self.root / 'lags.toml'
        config.write_text('[panel]\nlags = [1, 8]\n')
        self.assertEqual(load_lags(config), (1, 8))
        for value in ('[0]', '[1, 1]', '[true]', '[]', '[1.5]'):
            config.write_text(f'[panel]\nlags = {value}\n')
            with self.assertRaises(ValueError):
                load_lags(config)

    def test_eight_quarter_lag_crosses_years(self):
        self.write('bhi', ['lhd_name', 'year', 'quarter', 'pressure_index'],
                   [['Alpha', 2024, 3, .1], ['Alpha', 2026, 3, .4]])
        build_panels(*(self.root / p for p in ['bhi', 'socio', 'weather', 'map', 'ref']),
                     self.root / 'out', (1, 8))
        with (self.root / 'out/A.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[-1]['pressure_index__lag_8'], '0.1')
