import csv
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from tests import test_lhd_socioeconomic
from transform.socioeconomic.hospital_socioeconomic import (
    aggregate_hospitals, build_hospital_socioeconomic, map_sa1_to_hospitals,
)


class HospitalSocioeconomicTests(unittest.TestCase):
    def setUp(self):
        self.sa1 = gpd.GeoDataFrame({
            'SA1_CODE21': ['10102100701', '10102100702', '20102100701'],
            'STE_CODE21': ['1', '1', '2'],
        }, geometry=[box(0, 0, 2, 2), box(8, 0, 10, 2), box(0, 0, 2, 2)], crs=3577)
        locations = gpd.GeoSeries.from_xy([1, 9], [1, 1], crs=3577).to_crs(4326)
        self.hospitals = pd.DataFrame({
            'bhi_hospital_id': ['A', 'B'], 'hospital_name': ['Alpha', 'Beta'],
            'hospital_latitude': locations.y, 'hospital_longitude': locations.x,
        })

    def test_nearest_centroid_distance_filter_and_repeated_roles(self):
        hospitals = pd.concat([self.hospitals, self.hospitals])
        result = map_sa1_to_hospitals(self.sa1, hospitals)
        self.assertEqual(result.bhi_hospital_id.tolist(), ['A', 'B'])
        self.assertTrue((result.distance_km < 1e-8).all())
        # Both hospitals at the same point: stable identifier breaks ties.
        hospitals = self.hospitals.copy()
        hospitals.loc[1, ['hospital_latitude', 'hospital_longitude']] = hospitals.loc[0, ['hospital_latitude', 'hospital_longitude']].values
        result = map_sa1_to_hospitals(self.sa1, hospitals.iloc[::-1])
        self.assertEqual(result.bhi_hospital_id.tolist(), ['A', 'A'])
        self.assertAlmostEqual(result.distance_km.iloc[1], 0.008, places=7)

    def test_invalid_coordinates_conflicts_and_duplicate_sa1(self):
        invalid = self.hospitals.copy()
        invalid.loc[0, 'hospital_latitude'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'coordinates'):
            map_sa1_to_hospitals(self.sa1, invalid)
        conflict = self.hospitals.copy()
        conflict.loc[1, 'bhi_hospital_id'] = 'A'
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            map_sa1_to_hospitals(self.sa1, conflict)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            map_sa1_to_hospitals(pd.concat([self.sa1, self.sa1]), self.hospitals)

    def test_real_io_and_shared_aggregation(self):
        fixture = test_lhd_socioeconomic.LhdSocioeconomicTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        mapping = fixture.root / 'hospital_mapping.csv'
        mapping.write_text(fixture.mapping.read_text().replace('lhd_code,lhd_name', 'bhi_hospital_id,hospital_name'))
        alpha, beta = aggregate_hospitals(mapping, fixture.workbook)
        self.assertEqual(alpha['irsd_population_weighted_mean'], 950)
        self.assertEqual(alpha['excluded_sa1_count'], 1)
        self.assertIsNone(beta['irsd_mean'])
        sa1 = fixture.root / 'sa1.geojson'
        hospitals = fixture.root / 'hospitals.csv'
        output = fixture.root / 'summary.csv'
        self.sa1.to_file(sa1, driver='GeoJSON')
        self.hospitals.to_csv(hospitals, index=False)
        self.assertEqual(build_hospital_socioeconomic(sa1, hospitals, fixture.workbook, mapping, output), 2)
        with output.open() as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]['irsd_mean'], '800.0')
        self.assertEqual(rows[1]['population_sum'], '300.0')
        with self.assertRaisesRegex(ValueError, 'differ'):
            build_hospital_socioeconomic(sa1, hospitals, fixture.workbook, hospitals, output)
