import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box, Point

from data import load_data, load_weather, district_stations
from figures import make_map, clicked_lhd
from app import create_app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        gpd.GeoDataFrame({'lhd_code': ['A', 'B'], 'lhd_name': ['Alpha', 'Beta']},
            geometry=[box(140, -34, 141, -33), box(141, -34, 142, -33)], crs=4326).to_file(self.root / 'nsw_lhd_boundaries.geojson', driver='GeoJSON')
        gpd.GeoDataFrame({'SA1_CODE21': ['001', '002']},
            geometry=[box(140.1, -33.9, 140.2, -33.8), box(141.1, -33.9, 141.2, -33.8)], crs=4326).to_file(self.root / 'nsw_sa1_boundaries.geojson', driver='GeoJSON')
        self.mapping = self.root / 'sa1_to_lhd.csv'
        pd.DataFrame({'sa1_code_2021': ['001', '002'], 'lhd_code': ['A', 'B'],
            'overlap_pct': [100, 95], 'assignment_method': ['largest_area_overlap'] * 2}).to_csv(self.mapping, index=False)

    def test_selection_draws_only_csv_membership(self):
        data = load_data(self.root)
        figure = make_map(data, 'A')
        points = next(t for t in figure.data if t.name == 'SA1 centres')
        self.assertEqual(list(points.text), ['001'])
        links = next(t for t in figure.data if t.name == 'SA1 → LHD')
        self.assertEqual(len(links.x), 3)
        self.assertAlmostEqual(links.x[1], data['districts'][0]['x'])
        self.assertFalse(any(t.name == 'SA1 centres' for t in make_map(data).data))

    def test_missing_geometry_match_is_rejected(self):
        self.mapping.write_text('sa1_code_2021,lhd_code,overlap_pct,assignment_method\n999,A,100,largest_area_overlap\n')
        with self.assertRaisesRegex(ValueError, 'no boundary'):
            load_data(self.root)

    def weather_fixture(self):
        pd.DataFrame({'bhi_lhd_id': ['ALHD', 'BLHD'], 'boundary_code': ['A', 'B']}).to_csv(self.root / 'nsw_lhds.csv', index=False)
        gpd.GeoDataFrame({'bom_station_id': ['001234', '005678']},
            geometry=[Point(140.3, -33.5), Point(141.3, -33.5)], crs=4326).to_file(self.root / 'weather_stations.geojson', driver='GeoJSON')
        rows = [
            ['H1', 'One', 'ALHD', 'temperature', '001234', 'Station one', 'approved'],
            ['H1', 'One', 'ALHD', 'rainfall', '001234', 'Station one', 'approved'],
            ['H2', 'Two', 'ALHD', 'wind', '001234', 'Station one', 'approved'],
            ['H3', 'Three', 'BLHD', 'wind', '005678', 'Station two', 'approved'],
            ['H4', 'Specialty', '', 'rainfall', '005678', 'Station two', 'approved'],
            ['H5', 'Pending', 'ALHD', 'wind', '005678', 'Station two', 'pending'],
        ]
        path = self.root / 'hospital_weather_mapping.csv'
        pd.DataFrame(rows, columns=['bhi_hospital_id', 'hospital_name', 'geographic_lhd_id',
            'weather_observation_group', 'bom_station_id', 'bom_station_name', 'mapping_status']).to_csv(path, index=False)
        return path

    def test_weather_joins_deduplicates_and_retains_provenance(self):
        self.weather_fixture()
        weather = load_weather(self.root)
        stations = district_stations(weather, 'A')
        self.assertEqual(list(stations.bom_station_id), ['001234'])
        self.assertEqual(stations.iloc[0].hospital_count, 2)
        self.assertEqual(stations.iloc[0].groups, 'rainfall, temperature, wind')
        self.assertEqual(weather['unassigned_hospitals'], 1)
        self.assertEqual(weather['unapproved_rows'], 1)
        data = load_data(self.root)
        fig = make_map(data, 'A', 'weather', weather)
        links = next(t for t in fig.data if t.name == 'LHD → weather station')
        self.assertEqual(len(links.x), 3)
        self.assertEqual(links.x[0], data['districts'][0]['x'])
        self.assertAlmostEqual(links.x[1], stations.iloc[0].x)
        self.assertFalse(any(t.name == 'SA1 centres' for t in fig.data))
        self.assertFalse(any(t.name == 'Weather stations' for t in make_map(data, None, 'weather', weather).data))
        self.assertTrue(district_stations(weather, 'unknown').empty)

    def test_missing_weather_station_is_rejected(self):
        path = self.weather_fixture()
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame.loc[0, 'bom_station_id'] = '999999'
        frame.to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, 'missing from weather_stations'):
            load_weather(self.root)

    def test_unknown_weather_lhd_is_rejected(self):
        path = self.weather_fixture()
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame.loc[0, 'geographic_lhd_id'] = 'UNKNOWN'
        frame.to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, 'unknown geographic LHD'):
            load_weather(self.root)

    def test_click_ignores_non_district_points(self):
        self.assertEqual(clicked_lhd({'points': [{'customdata': 'A'}]}, {'A'}), 'A')
        for click in [None, {'points': []}, {'points': [{'text': '001'}]}, {'points': [{'customdata': 'unknown'}]}]:
            self.assertIsNone(clicked_lhd(click, {'A'}))

    def test_app_serves_layout_and_callbacks(self):
        client = create_app().server.test_client()
        for path in ['/', '/_dash-layout', '/_dash-dependencies', '/assets/style.css']:
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            response.close()
        self.assertIn(b'Data Engineering', client.get('/_dash-layout').data)


if __name__ == '__main__':
    unittest.main()
