import unittest

import pandas as pd

from app import create_app
from trends import ALL, load_trends, scope_data, trend_figure, pressure_map


class TrendsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames, cls.boundaries, cls.districts = load_trends()

    def test_geographic_drilldown_and_map_values(self):
        for scope in [ALL, *self.districts.lhd_name]:
            frame = scope_data(self.frames, scope)
            self.assertFalse(frame.empty)
            if scope != ALL:
                self.assertTrue(frame.geographic_lhd_name.eq(scope).all())
            latest = frame.loc[frame.period == frame.period.max()]
            fig = pressure_map(latest, self.boundaries, scope, None)
            mapped = latest.dropna(subset=['x', 'y', 'pressure_index'])
            self.assertEqual(list(fig.data[-1].customdata), list(mapped.entity))
            self.assertEqual(list(fig.data[-1].marker.size), list(mapped.pressure_index))

    def test_calendar_window_and_moving_average_do_not_bridge_gaps(self):
        periods = pd.period_range('2022Q1', periods=16, freq='Q')
        frame = pd.DataFrame({'entity': 'Test', 'period': periods, 'pressure_index': [i / 20 for i in range(16)]})
        fig = trend_figure(frame, 'Test', 'pressure_index', periods[-1])
        self.assertEqual(len(fig.data[0].x), 12)
        self.assertAlmostEqual(fig.data[1].y[0], sum([.05, .10, .15, .20]) / 4)
        frame = frame.drop(index=10)
        fig = trend_figure(frame, 'Test', 'pressure_index', periods[-1])
        self.assertTrue(pd.isna(fig.data[0].y[6]))
        self.assertTrue(all(pd.isna(fig.data[1].y[i]) for i in range(6, 10)))

    def test_chart_axis_fits_small_values_and_rounds_above_maximum(self):
        periods = pd.period_range('2023Q1', periods=12, freq='Q')
        frame = pd.DataFrame({'entity': 'Test', 'period': periods,
                              'pressure_index': [.21, .22, .23] * 4,
                              'test__minutes': [2, 3, 4] * 4})
        pressure = trend_figure(frame, 'Test', 'pressure_index', periods[-1])
        low, high = pressure.layout.yaxis.range
        self.assertGreater(low, 0)
        self.assertLess(low, .21)
        self.assertGreater(high, .23)
        self.assertLess(high, .3)
        minutes = trend_figure(frame, 'Test', 'test__minutes', periods[-1])
        self.assertEqual(minutes.layout.yaxis.range[1] % 1, 0)
        self.assertGreater(minutes.layout.yaxis.range[1], 4)
        frame['pressure_index'] = 0
        constant = trend_figure(frame, 'Test', 'pressure_index', periods[-1])
        self.assertEqual(constant.layout.yaxis.range[0], 0)
        self.assertGreater(constant.layout.yaxis.range[1], 0)

    def test_page_callbacks_render_all_scopes_and_handle_map_click(self):
        app = create_app()
        client = app.server.test_client()
        self.assertEqual(client.get('/current-trends').status_code, 200)
        key = next(k for k in app.callback_map if 'trends-map.figure' in k)
        output = app.callback_map[key]['output']
        for scope in [ALL, *self.districts.lhd_name]:
            frame = scope_data(self.frames, scope)
            response = client.post('/_dash-update-component', json={
                'output': key, 'outputs': [{'id': o.component_id, 'property': o.component_property} for o in output],
                'changedPropIds': ['trends-scope.value'], 'inputs': [
                    {'id': 'trends-scope', 'property': 'value', 'value': scope},
                    {'id': 'trends-entity', 'property': 'value', 'value': frame.entity.iloc[0]},
                    {'id': 'trends-measure', 'property': 'value', 'value': 'pressure_index'},
                    {'id': 'trends-refresh', 'property': 'n_clicks', 'value': 0}], 'state': []})
            self.assertEqual(response.status_code, 200)
            data = response.json['response']
            self.assertNotEqual(data['trends-quarter']['children'], 'Unavailable', data)
            self.assertEqual(len(data['trends-chart']['figure']['data']), 2)
        key = next(k for k in app.callback_map if 'trends-entity.data' in k)
        entity = self.frames['lhd'].entity.iloc[-1]
        response = client.post('/_dash-update-component', json={
            'output': key, 'outputs': [{'id': o.component_id, 'property': o.component_property} for o in app.callback_map[key]['output']],
            'changedPropIds': ['trends-map.clickData'], 'inputs': [
                {'id': 'trends-scope', 'property': 'value', 'value': ALL},
                {'id': 'trends-map', 'property': 'clickData', 'value': {'points': [{'customdata': entity}]}},
                {'id': 'trends-refresh', 'property': 'n_clicks', 'value': 0}], 'state': [
                {'id': 'trends-entity', 'property': 'value', 'value': None},
                {'id': 'trends-measure', 'property': 'value', 'value': 'pressure_index'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['response']['trends-entity']['value'], entity)
        route = next(v['callback'].__wrapped__ for k, v in app.callback_map.items() if 'engineering-page.style' in k)
        self.assertEqual(route('/current-trends')[4], {})
