import json
import unittest

import pandas as pd

from app import create_app
from forecasting import forecast_measures, load_forecasts
from trends import ALL, ROOT, layout, measure_options, scope_data, trend_figure


class ForecastingTests(unittest.TestCase):
    def test_forecast_reuses_trends_grid_styles(self):
        page = layout(forecast=True)
        self.assertEqual(page.id, 'forecast-page')
        grid = next(child for child in page.children if getattr(child, 'className', '') == 'trends-grid')
        self.assertEqual(grid.children[1].children[-1].className, 'trends-ranking')

    def test_measures_require_both_trained_target_and_prediction(self):
        frame = pd.DataFrame(columns=['pressure_index', 'extra__feature', 'trained__measure'])
        config = {'targets': {'pressure_index': {}, 'trained__measure': {}, 'missing__measure': {}}}
        self.assertEqual(forecast_measures(frame, config), ['pressure_index', 'trained__measure'])
        with self.assertRaises(ValueError):
            forecast_measures(frame, {'targets': {}})

    def test_forecasts_match_sources_and_chart_has_eleven_historical_quarters(self):
        frames, _, _ = load_forecasts()
        for level, folder in [('lhd', 'LHDs'), ('hospital', 'Hospitals')]:
            source = pd.read_csv(ROOT / 'gold' / 'xgboost' / folder / 'next_quarter_predictions.csv')
            config = json.loads((ROOT / 'gold' / 'xgboost' / folder / 'training_hyperparameters.json').read_text())
            frame = frames[level]
            end = frame.period.max()
            latest = frame.loc[frame.period == end]
            self.assertEqual(set(o['value'] for o in measure_options(frame)), set(config['targets']) & set(source))
            self.assertEqual(sorted(latest.pressure_index), sorted(source.pressure_index))
            entity = latest.entity.iloc[0]
            for option in measure_options(frame):
                figure = trend_figure(frame, entity, option['value'], end, forecast=True)
                self.assertEqual(len(figure.data[0].x), 12)
                expected = frame.loc[(frame.entity == entity) & (frame.period < end)].set_index('period')[option['value']].reindex(pd.period_range(end=end - 1, periods=11, freq='Q'))
                pd.testing.assert_series_equal(pd.Series(figure.data[0].y[:-1]), pd.Series(expected.to_numpy()), check_names=False)
                self.assertTrue(pd.isna(figure.data[0].y[-1]))
                self.assertEqual(figure.data[-1].x[-1], str(end))
                self.assertEqual(figure.data[-1].y[-1], latest.set_index('entity').loc[entity, option['value']])

    def test_route_callbacks_forecast_values_and_reject_historical_only_measure(self):
        app = create_app()
        client = app.server.test_client()
        route = next(v['callback'].__wrapped__ for k, v in app.callback_map.items() if 'engineering-page.style' in k)
        self.assertEqual(route('/forecasting')[6], {})
        self.assertEqual(route('/forecasting')[4], {'display': 'none'})
        frames, _, districts = load_forecasts()
        update = next(v['callback'].__wrapped__ for k, v in app.callback_map.items() if 'forecast-map.figure' in k)
        for scope in [ALL, *districts.lhd_name]:
            frame = scope_data(frames, scope)
            result = update(scope, frame.entity.iloc[0], 'unavailable__measure', 0)
            self.assertEqual(result[4], str(frame.period.max()))
            latest = frame.loc[frame.period == frame.period.max()].sort_values('pressure_index', ascending=False)
            mapped = latest.dropna(subset=['x', 'y', 'pressure_index'])
            self.assertEqual(list(result[0].data[-1].marker.color), list(mapped.pressure_index))
            self.assertEqual(result[1].data[-1].name, 'Forecast')
            rows = result[2].children[1].children
            self.assertEqual(rows[0].children[1].children, format(latest.pressure_index.iloc[0], '.3f'))
        self.assertEqual(client.get('/forecasting').status_code, 200)
        self.assertEqual(client.get('/_dash-layout').status_code, 200)
