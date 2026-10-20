from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from transform.bhi.pressure_index import calculate_pressure_index, load_weights


class PressureIndexTests(unittest.TestCase):
    def test_defaults_and_endpoints(self):
        weights = load_weights()
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertEqual(calculate_pressure_index(dict.fromkeys(weights, "100"), weights), 0)
        self.assertEqual(calculate_pressure_index(dict.fromkeys(weights, "0"), weights), 1)
        self.assertEqual(calculate_pressure_index({}, weights), "")

    def test_normalizes_weights_and_ignores_disabled_measures(self):
        weights = {"a": 2, "b": 3, "c": 0}
        self.assertAlmostEqual(calculate_pressure_index({"a": "80", "b": "40", "c": "999"}, weights), 0.44)
        self.assertAlmostEqual(calculate_pressure_index({"a": "80"}, weights), 0.2)

    def test_invalid_percentages(self):
        for value in ("-1", "101", "nan", "inf", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_pressure_index({"a": value}, {"a": 1})

    def test_invalid_configuration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "weights.toml"
            for content in ('', '[[measures]]\ncolumn="a"\nweight=0',
                            '[[measures]]\ncolumn="a"\nweight=-1',
                            '[[measures]]\ncolumn="a"\nweight=nan',
                            '[[measures]]\ncolumn="a"\nweight=true',
                            '[[measures]]\ncolumn="a"\nweight=1\n[[measures]]\ncolumn="a"\nweight=2'):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        load_weights(path)
