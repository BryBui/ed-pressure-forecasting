"""Calendar alignment, grouped chronological validation and model I/O."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'src/xgboost/LHDs/train_xgboost_lhd.py'
spec = importlib.util.spec_from_file_location('train_lhd', SCRIPT)
training = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training)


class TrainingTests(unittest.TestCase):
    def test_default_targets_match_overall_source_and_pressure_index(self):
        import re
        with training.DEFAULT_CONFIG.open('rb') as stream:
            config = training.tomllib.load(stream)
        with (training.ROOT / 'data/silver/bhi/overall_results.csv').open() as stream:
            measures = {(r['measure_name'], r['metric']) for r in csv.DictReader(stream)}
        def slug(value):
            return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')
        expected = {'overall__total__total__' + slug(name) + '__' +
                    ('percent' if metric == '%' else slug(metric))
                    for name, metric in measures}
        self.assertEqual(set(config['targets']['columns']), expected | {'pressure_index'})

    def test_exact_quarter_join_and_lhd_isolation(self):
        rows = [dict(lhd_id=lhd, year='2020', quarter=str(q), value=str(v), _period=8080+q-1)
                for lhd, q, v in [('A', 1, 10), ('A', 3, 30), ('B', 1, 100), ('B', 2, 200)]]
        x, y, features = training.prepare(rows, set(rows[0]) - {'_period'}, ['value'])
        self.assertTrue(np.isnan(y[0, 0]))
        self.assertEqual(y[2, 0], 200)
        self.assertTrue(np.isnan(y[3, 0]))
        self.assertEqual(x[0, features.index('value')], 10)

    def test_splits_group_lhds_and_exclude_future(self):
        periods = np.repeat(np.arange(30), 3)
        splits = training.rolling_splits(periods, 3, 2, 20)
        for tr, va in splits:
            self.assertLess(periods[tr].max(), periods[va].min())
            self.assertEqual(va.sum(), 6)
        with self.assertRaises(ValueError):
            training.rolling_splits(periods, 3, 8, 20)

    def test_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'A.csv'
            p.write_text('lhd_id,year,quarter,value\nA,2020,1,10\nA,2020,1,20\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate LHD-quarter'):
                training.read_panels(Path(tmp))

    def test_training_and_saved_model(self):
        import xgboost as xgb
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            panels = root / 'panels'
            panels.mkdir()
            for lhd in ['A', 'B']:
                with (panels / f'{lhd}.csv').open('w', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(['lhd_id', 'lhd_name', 'year', 'quarter', 'value', 'discontinued'])
                    for i in range(16):
                        writer.writerow([lhd, lhd, 2020+i//4, i%4+1, i+1, i+2 if i < 12 else ''])
            config = root / 'config.toml'
            config.write_text('''[data]
panels = "unused"
output = "unused"
[targets]
columns = ["value", "discontinued"]
[validation]
folds = 2
validation_quarters = 2
min_train_quarters = 4
[model]
objective = "reg:squarederror"
tree_method = "hist"
nthread = 1
[hyperparameters]
num_boost_round = 2
max_depth = 1
[[search.candidates]]
num_boost_round = 2
max_depth = 1
''')
            output = root / 'output'
            model_dir = root / 'models'
            report = training.train(config, panels, output, tune=True, model_dir=model_dir)
            self.assertEqual(report['targets']['value']['training_rows'], 30)
            model = xgb.Booster()
            model.load_model(model_dir / 'value.ubj')
            self.assertFalse(list(output.glob('*.ubj')))
            self.assertEqual(model.num_boosted_rounds(), 2)
            with (output / 'next_quarter_predictions.csv').open() as stream:
                forecasts = list(csv.DictReader(stream))
            self.assertEqual(len(forecasts), 2)
            self.assertEqual((forecasts[0]['year'], forecasts[0]['quarter']), ('2024', '1'))
            json.loads((output / 'training_report.json').read_text())
            parameters = json.loads((output / 'training_hyperparameters.json').read_text())
            self.assertEqual(parameters['targets']['value'], {
                'objective': 'reg:squarederror', 'tree_method': 'hist',
                'nthread': 1, 'num_boost_round': 2, 'max_depth': 1})
            self.assertEqual(parameters['validation']['folds'], 2)



if __name__ == '__main__':
    unittest.main()
