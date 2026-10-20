import tempfile
import unittest
from pathlib import Path

from app import create_app
from socioeconomic import load_socioeconomic, figures


class SocioeconomicTests(unittest.TestCase):
    def test_ranked_scores_and_heatmap_match_source(self):
        frame = load_socioeconomic()
        for method in ('mean', 'population_weighted_mean'):
            rank, heat = figures(frame, 'irsd', method, frame.iloc[0].lhd_code)
            ordered = frame.sort_values(f'irsd_{method}', ascending=False)
            self.assertEqual(list(rank.data[0].x), list(ordered[f'irsd_{method}']))
            self.assertEqual(list(rank.data[0].customdata[:, 0]), list(ordered.lhd_code))
            self.assertEqual(heat.data[0].customdata.shape, (len(frame), 4))
            self.assertEqual(list(heat.data[0].customdata[:, 0]), list(ordered[f'irsd_{method}']))

    def test_invalid_counts_are_rejected(self):
        frame = load_socioeconomic()
        frame.loc[0, 'excluded_sa1_count'] += 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.csv'
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, 'sum to mapped'):
                load_socioeconomic(path)

    def test_circle_area_tracks_population_independently_of_selection(self):
        frame = load_socioeconomic()
        frame.loc[frame.index[:3], 'population_sum'] = [0, 100_000, 400_000]
        selected = frame.iloc[1].lhd_code
        rank, _ = figures(frame, 'irsd', 'mean', selected)
        unselected, _ = figures(frame, 'irsd', 'mean', None)
        ordered = frame.sort_values('irsd_mean', ascending=False)
        marker = rank.data[0].marker
        self.assertEqual(marker.sizemode, 'area')
        self.assertEqual(list(marker.size), list(ordered.population_sum))
        self.assertEqual(list(marker.size), list(unselected.data[0].marker.size))
        self.assertEqual(marker.sizeref, unselected.data[0].marker.sizeref)
        position = list(ordered.lhd_code).index(selected)
        self.assertEqual(marker.line.color[position], '#254456')
        self.assertEqual(marker.line.width[position], 3)

        frame['population_sum'] = 0
        empty, _ = figures(frame, 'irsd', 'mean', None)
        self.assertGreater(empty.data[0].marker.sizeref, 0)

    def test_page_routing_and_update_callback(self):
        app = create_app()
        client = app.server.test_client()
        self.assertEqual(client.get('/socioeconomic').status_code, 200)
        key = next(k for k in app.callback_map if 'socio-metrics.children' in k)
        outputs = [{'id': o.component_id, 'property': o.component_property} for o in app.callback_map[key]['output']]
        for selected in (None, load_socioeconomic().iloc[0].lhd_code):
            response = client.post('/_dash-update-component', json={
                'output': key, 'outputs': outputs, 'changedPropIds': ['socio-index.value'],
                'inputs': [{'id': 'socio-index', 'property': 'value', 'value': 'irsd'},
                           {'id': 'socio-method', 'property': 'value', 'value': 'population_weighted_mean'},
                           {'id': 'socio-district', 'property': 'value', 'value': selected},
                           {'id': 'socio-refresh', 'property': 'n_clicks', 'value': 0}], 'state': []})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['response']['socio-error']['children'], [])
            self.assertEqual(len(response.json['response']['socio-district']['data']), 15)
        route_key = next(k for k in app.callback_map if 'engineering-page.style' in k)
        route = app.callback_map[route_key]['callback'].__wrapped__
        self.assertEqual(route('/socioeconomic')[0], {'display': 'none'})
        self.assertEqual(route('/')[1], {'display': 'none'})
