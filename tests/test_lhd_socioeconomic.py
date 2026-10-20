import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from transform.socioeconomic.lhd_socioeconomic import aggregate_lhds, build_lhd_socioeconomic


class LhdSocioeconomicTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.mapping = self.root / 'mapping.csv'
        self.mapping.write_text(
            'sa1_code_2021,lhd_code,lhd_name\n'
            '10102100701,A,Alpha\n10102100702,A,Alpha\n'
            '10102100703,A,Alpha\n10102100704,B,Beta\n'
            '10102100705,B,Beta\n'
        )
        self.workbook = self.root / 'seifa.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Table 1'
        for _ in range(5):
            sheet.append([])
        sheet.append(['2021 Statistical Area Level 1 (SA1)', 'Score', 'Decile',
                      'Score', 'Decile', 'Score', 'Decile', 'Score', 'Decile',
                      'Usual Resident Population'])
        sheet.append([10102100701, 800, 1, 900, 1, 1000, 1, 1100, 1, 100])
        sheet.append([10102100702, 1000, 2, 1100, 2, 1200, 2, 1300, 2, 300])
        sheet.append([10102100703, None, None, 900, 1, 1000, 1, 1100, 1, 500])
        sheet.append([10102100704, None, None, None, None, None, None, None, None, 50])
        workbook.save(self.workbook)
        workbook.close()

    def test_means_population_and_whole_sa1_exclusion(self):
        alpha, beta = aggregate_lhds(self.mapping, self.workbook)
        self.assertEqual(alpha['population_sum'], 400)
        self.assertEqual(alpha['included_sa1_count'], 2)
        self.assertEqual(alpha['excluded_sa1_count'], 1)
        for index, simple, weighted in [('irsd', 900, 950), ('irsad', 1000, 1050),
                                        ('ier', 1100, 1150), ('ieo', 1200, 1250)]:
            self.assertEqual(alpha[f'{index}_mean'], simple)
            self.assertEqual(alpha[f'{index}_population_weighted_mean'], weighted)
        self.assertEqual(beta['excluded_sa1_count'], 2)
        self.assertEqual(beta['population_sum'], 0)
        self.assertIsNone(beta['irsd_mean'])
        self.assertIsNone(beta['irsd_population_weighted_mean'])

    def test_csv_output_and_input_protection(self):
        output = self.root / 'processed' / 'summary.csv'
        self.assertEqual(build_lhd_socioeconomic(self.mapping, self.workbook, output), 2)
        with output.open(newline='') as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]['irsd_population_weighted_mean'], '950.0')
        self.assertEqual(rows[1]['irsd_mean'], '')
        with self.assertRaisesRegex(ValueError, 'differ'):
            build_lhd_socioeconomic(self.mapping, self.workbook, self.mapping)

    def test_duplicate_mapping_rejected(self):
        with self.mapping.open('a') as destination:
            destination.write('10102100701,B,Beta\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate mapped SA1'):
            aggregate_lhds(self.mapping, self.workbook)


if __name__ == '__main__':
    unittest.main()
