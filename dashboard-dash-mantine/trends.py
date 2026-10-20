"""Read-only current-quarter pressure explorer and linked historical trends."""
from pathlib import Path
from functools import lru_cache
import math

import geopandas as gpd
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html, no_update
import dash_mantine_components as dmc

ROOT = Path(__file__).resolve().parents[1] / 'data'
ALL = 'all'


def resolve_data_root(root):
    """Pin one published release for every file read by a dashboard request."""
    root = Path(root)
    current = root / '.pipeline/current/data'
    return current.resolve() if current.is_dir() else root


def load_trends(root=ROOT):
    root = resolve_data_root(root)
    files = ['silver/linking/lhd_bhi_lags.csv', 'silver/linking/hospital_bhi_lags.csv',
             'reference/nsw_lhds.csv', 'reference/nsw_hospitals.csv',
             'reference/nsw_lhd_boundaries.geojson', 'reference/hospital_weather_mapping.csv']
    signature = tuple((root / f).stat().st_mtime_ns for f in files)
    return _load_trends(str(root), signature)


@lru_cache(maxsize=1)
def _load_trends(root, signature):
    root = Path(root)
    frames = {}
    for level in ('lhd', 'hospital'):
        frame = pd.read_csv(root / 'silver' / 'linking' / f'{level}_bhi_lags.csv')
        name = f'{level}_name'
        if frame.duplicated([name, 'year', 'quarter']).any():
            raise ValueError(f'Duplicate {level} quarters.')
        if not frame.quarter.isin([1, 2, 3, 4]).all():
            raise ValueError('Invalid quarter.')
        frame['period'] = pd.PeriodIndex(frame.year.astype(str) + 'Q' + frame.quarter.astype(str), freq='Q')
        if not frame.pressure_index.dropna().between(0, 1).all():
            raise ValueError('Pressure index must be between zero and one.')
        frames[level] = frame.rename(columns={name: 'entity'}).sort_values('period')
    bridge = pd.read_csv(root / 'reference' / 'nsw_lhds.csv')
    boundaries = gpd.read_file(root / 'reference' / 'nsw_lhd_boundaries.geojson').to_crs(3577)
    boundaries['x'] = boundaries.geometry.centroid.x / 1000
    boundaries['y'] = boundaries.geometry.centroid.y / 1000
    districts = bridge.merge(boundaries[['lhd_code', 'x', 'y']], left_on='boundary_code', right_on='lhd_code', validate='one_to_one')
    hospitals = pd.read_csv(root / 'reference' / 'nsw_hospitals.csv')
    coords = pd.read_csv(root / 'reference' / 'hospital_weather_mapping.csv')[
        ['bhi_hospital_id', 'hospital_latitude', 'hospital_longitude']].drop_duplicates()
    if coords.bhi_hospital_id.duplicated().any():
        raise ValueError('Conflicting hospital coordinates.')
    hospitals = hospitals.merge(coords, on='bhi_hospital_id', how='left', validate='one_to_one')
    points = gpd.GeoSeries(gpd.points_from_xy(hospitals.hospital_longitude, hospitals.hospital_latitude), crs=4326).to_crs(3577)
    hospitals['x'], hospitals['y'] = points.x / 1000, points.y / 1000
    frames['lhd'] = frames['lhd'].merge(districts[['lhd_name', 'x', 'y']], left_on='entity', right_on='lhd_name', how='left', validate='many_to_one')
    frames['hospital'] = frames['hospital'].merge(hospitals[['hospital_name', 'geographic_lhd_name', 'x', 'y']], left_on='entity', right_on='hospital_name', how='left', validate='many_to_one')
    # Simplify only the display geometry after calculating original-polygon centres.
    boundaries.geometry = boundaries.geometry.simplify(300)
    return frames, boundaries, districts


def scope_data(frames, scope):
    return frames['lhd'] if scope == ALL else frames['hospital'].loc[frames['hospital'].geographic_lhd_name == scope]


def measure_options(frame):
    options = [{'value': 'pressure_index', 'label': 'Pressure index · 0–1'}]
    for col in frame.columns:
        if '__' in col and frame[col].notna().any():
            parts = col.split('__')
            context = ' · '.join(p.replace('_', ' ') for p in parts[:3] if p not in ('overall', 'total'))
            label = parts[-2].replace('_', ' ').capitalize()
            options.append({'value': col, 'label': f'{context + " · " if context else ""}{label} ({parts[-1]})'})
    return options


def window(frame, entity, end):
    periods = pd.period_range(end=end, periods=12, freq='Q')
    # Reindex before rolling so missing calendar quarters are never bridged.
    history = frame.loc[frame.entity == entity].set_index('period').reindex(pd.period_range(end=end, periods=15, freq='Q'))
    return history, periods


def trend_figure(frame, entity, measure, end, forecast=False):
    history, periods = window(frame, entity, end)
    values = history[measure]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=periods.astype(str), y=values.reindex(periods), mode='lines+markers', name='Quarterly',
                            line={'color': '#128b88', 'width': 3}, connectgaps=False))
    fig.add_trace(go.Scatter(x=periods.astype(str), y=values.rolling(4, min_periods=4).mean().reindex(periods),
                            name='4-quarter moving average', line={'color': '#e6a14b', 'width': 3, 'dash': 'dot'}, connectgaps=False))
    if forecast:
        fig.data[0].name = 'Historical'
        fig.data[0].y = values.reindex(periods).where(periods < end)
        fig.add_trace(go.Scatter(x=periods[-2:].astype(str), y=values.reindex(periods[-2:]),
                                mode='lines+markers', name='Forecast',
                                line={'color': '#b34367', 'width': 3, 'dash': 'dash'},
                                marker={'symbol': 'diamond', 'size': 10}, connectgaps=False))
    fig.update_layout(template='plotly_white', title=entity or 'Choose a location', height=360,
                      margin={'l': 55, 'r': 20, 't': 55, 'b': 45}, hovermode='x unified',
                      legend={'orientation': 'h', 'y': 1.17}, yaxis_title='Pressure index (0–1)' if measure == 'pressure_index' else measure.split('__')[-1].capitalize())
    displayed = pd.concat([values.reindex(periods), values.rolling(4, min_periods=4).mean().reindex(periods)]).dropna()
    if not displayed.empty:
        low, high = float(displayed.min()), float(displayed.max())
        # Decimal bounds keep the 0–1 index readable; other measures round to integers.
        step = 0.01 if measure == 'pressure_index' else 1
        padding = max((high - low) * 0.05, step)
        lower = max(0, math.floor((low - padding) / step) * step)
        upper = (math.floor((high + padding) / step) + 1) * step
        fig.update_yaxes(range=[lower, upper], autorange=False)
    return fig


def pressure_map(latest, boundaries, scope, selected):
    fig = go.Figure()
    for row in boundaries.itertuples():
        geom = row.geometry
        polygons = list(geom.geoms) if geom.geom_type == 'MultiPolygon' else [geom]
        x, y = [], []
        for polygon in polygons:
            px, py = polygon.exterior.xy
            x.extend([v / 1000 for v in px] + [None]); y.extend([v / 1000 for v in py] + [None])
        fig.add_trace(go.Scatter(x=x, y=y, mode='lines', fill='toself', fillcolor='#edf3f5',
                                line={'color': '#b6cbd2', 'width': 0.8}, hoverinfo='skip', showlegend=False))
    mapped = latest.dropna(subset=['x', 'y', 'pressure_index'])
    fig.add_trace(go.Scatter(x=mapped.x, y=mapped.y, customdata=mapped.entity, text=mapped.entity,
        mode='markers', name='Pressure index', marker={'size': mapped.pressure_index, 'sizemode': 'area',
        'sizeref': 2 / 48**2, 'sizemin': 5, 'color': mapped.pressure_index, 'cmin': 0, 'cmax': 1,
        'colorscale': [[0, '#73d4c1'], [.5, '#e7b567'], [1, '#b34367']], 'showscale': True,
        'colorbar': {'title': 'Pressure'}, 'line': {'width': [3 if e == selected else 1 for e in mapped.entity], 'color': '#173347'}},
        hovertemplate='%{text}<br>Pressure: %{marker.color:.3f}<extra>Click to explore</extra>'))
    fig.update_layout(template='plotly_white', height=470, paper_bgcolor='white', plot_bgcolor='#f7fafb',
                      margin={'l': 10, 'r': 10, 't': 10, 'b': 10}, clickmode='event', dragmode='pan', uirevision=scope,
                      xaxis={'visible': False}, yaxis={'visible': False, 'scaleanchor': 'x'})
    if scope != ALL and not mapped.empty:
        fig.update_xaxes(range=[mapped.x.min() - 20, mapped.x.max() + 20])
        fig.update_yaxes(range=[mapped.y.min() - 20, mapped.y.max() + 20])
    return fig


def card(children, **kwargs):
    return dmc.Paper(children, p='lg', radius='lg', withBorder=True, **kwargs)


def layout(forecast=False):
    prefix = 'forecast' if forecast else 'trends'
    return html.Main([
        html.Div([dmc.Text('OBSERVE / COMPARE / EXPLORE', size='xs', fw=800, c='teal'),
                  dmc.Title('Forecasting' if forecast else 'Current Trends', order=1),
                  dmc.Text('Next-quarter forecasts for NSW emergency department pressure.' if forecast else 'A quarterly pulse on NSW emergency department pressure.', c='dimmed'),
                  dmc.Group([dmc.Select(id=f'{prefix}-scope', label='Explore a Local Health District', value=ALL,
                                      data=[{'value': ALL, 'label': 'All LHDs'}], searchable=True, allowDeselect=False,
                                      style={'width': 'min(100%, 440px)'}),
                             dmc.Button('Refresh data', id=f'{prefix}-refresh', variant='light')], align='end', mt='lg')], className='socio-hero trends-hero'),
        html.Div(id=f'{prefix}-error'), html.Div(id=f'{prefix}-metrics', className='metrics'),
        html.Div([card([dmc.Group([dmc.Title('Pressure across the map', order=3), dmc.Badge(id=f'{prefix}-quarter', color='teal')], justify='space-between'),
                       dmc.Text('Circle area represents pressure, on a fixed 0–1 scale. Click a circle to inspect its history.', size='sm', c='dimmed'),
                       dcc.Graph(id=f'{prefix}-map', config={'displaylogo': False, 'scrollZoom': True}),
                       dmc.Select(id=f'{prefix}-entity', label='LHD / hospital', data=[], searchable=True, allowDeselect=False)]),
                  card([dmc.Text('FORECAST QUARTER' if forecast else 'LATEST QUARTER', size='xs', fw=800, c='teal'), dmc.Title('Pressure comparison', order=3),
                        dmc.Text('Highest pressure first · Δ from previous quarter', size='sm', c='dimmed', mb='md'),
                        html.Div(id=f'{prefix}-ranking', className='trends-ranking')])], className='trends-grid'),
        card([dmc.Group([dmc.Title('11 historical quarters + forecast' if forecast else 'The last 12 quarters', order=3),
                        dmc.Select(id=f'{prefix}-measure', label='ED measure', data=[{'value': 'pressure_index', 'label': 'Pressure index · 0–1'}],
                                   value='pressure_index', searchable=True, allowDeselect=False, style={'width': 'min(100%, 540px)'})], justify='space-between'),
              dcc.Graph(id=f'{prefix}-chart', config={'displaylogo': False}),
              dmc.Text(('Diamond and dashed line: next-quarter forecast. ' if forecast else '') + 'Dotted line: trailing four-quarter average, requiring four reported quarters. Gaps indicate unavailable data.', size='sm', c='dimmed')], mt='lg'),
        dmc.Text(id=f'{prefix}-notes', size='sm', c='dimmed', mt='lg'),
        dmc.Text(('Source: XGBoost next-quarter predictions + BHI Silver quarterly panels · ' if forecast else 'Source: BHI Silver quarterly panels · ') + 'pressure index is the stored composite of timely-care shortfalls; higher means more pressure. Available components are reweighted when inputs are missing. No clinical thresholds are implied.', size='xs', c='dimmed', mt='md')
    ], id=f'{prefix}-page', className='main', style={'display': 'none'})


def register_callbacks(app, forecast=False):
    prefix = 'forecast' if forecast else 'trends'
    if forecast:
        from forecasting import load_forecasts
        loader = load_forecasts
    else:
        loader = load_trends
    @app.callback(Output(f'{prefix}-scope', 'data'), Output(f'{prefix}-entity', 'data'), Output(f'{prefix}-entity', 'value'),
                  Output(f'{prefix}-measure', 'data'), Output(f'{prefix}-measure', 'value'), Output(f'{prefix}-error', 'children'),
                  Input(f'{prefix}-scope', 'value'), Input(f'{prefix}-map', 'clickData'), Input(f'{prefix}-refresh', 'n_clicks'),
                  State(f'{prefix}-entity', 'value'), State(f'{prefix}-measure', 'value'))
    def selections(scope, click, _, selected, measure):
        try:
            frames, _, districts = loader()
            frame = scope_data(frames, scope)
            entities = sorted(frame.entity.unique())
            options = measure_options(frame)
            if ctx.triggered_id == f'{prefix}-map' and click and click.get('points'):
                candidate = click['points'][0].get('customdata')
                if isinstance(candidate, str) and candidate in entities:
                    selected = candidate
            return ([{'value': ALL, 'label': 'All LHDs'}] + [{'value': n, 'label': n} for n in sorted(districts.lhd_name)],
                    entities, selected if selected in entities else next(iter(entities), None), options,
                    measure if measure in {o['value'] for o in options} else 'pressure_index', [])
        except (OSError, ValueError, KeyError) as exc:
            return no_update, [], None, no_update, 'pressure_index', dmc.Alert(str(exc), color='red')

    @app.callback(Output(f'{prefix}-map', 'figure'), Output(f'{prefix}-chart', 'figure'), Output(f'{prefix}-ranking', 'children'),
                  Output(f'{prefix}-metrics', 'children'), Output(f'{prefix}-quarter', 'children'), Output(f'{prefix}-notes', 'children'),
                  Input(f'{prefix}-scope', 'value'), Input(f'{prefix}-entity', 'value'), Input(f'{prefix}-measure', 'value'), Input(f'{prefix}-refresh', 'n_clicks'))
    def update(scope, selected, measure, _):
        try:
            frames, boundaries, _ = loader()
            frame = scope_data(frames, scope)
            end = frames['lhd' if scope == ALL else 'hospital'].period.max()
            latest = frame.loc[frame.period == end].copy()
            previous = frame.loc[frame.period == end - 1].set_index('entity').pressure_index
            latest['delta'] = latest.pressure_index - latest.entity.map(previous)
            latest = latest.sort_values('pressure_index', ascending=False, na_position='last')
            if measure not in {o['value'] for o in measure_options(frame)}:
                measure = 'pressure_index'
            if selected not in set(frame.entity):
                selected = next(iter(sorted(frame.entity.unique())), None)
            def fmt(value, delta=False):
                return '—' if pd.isna(value) else format(value, '+.3f' if delta else '.3f')
            table = dmc.Table([dmc.TableThead(dmc.TableTr([dmc.TableTh(x) for x in ['Location', 'Index', 'Δ QoQ']])),
                               dmc.TableTbody([dmc.TableTr([dmc.TableTd(r.entity), dmc.TableTd(fmt(r.pressure_index)),
                                   dmc.TableTd(fmt(r.delta, True), style={'color': '#b34367' if r.delta > 0 else '#128b88'})]) for r in latest.itertuples()])], striped=True, highlightOnHover=True)
            valid = latest.pressure_index.notna().sum()
            stats = [('FORECAST QUARTER' if forecast else 'REPORTING QUARTER', str(end)), ('LOCATIONS FORECAST' if forecast else 'LOCATIONS REPORTING', f'{valid} / {len(latest)}'),
                     ('MEDIAN PRESSURE', fmt(latest.pressure_index.median())), ('PRESSURE INCREASED', str((latest.delta > 0).sum()))]
            metrics = [card([dmc.Text(k, size='xs', fw=700, c='dimmed'), dmc.Title(v, order=2, mt='xs')]) for k, v in stats]
            missing = latest[['x', 'y']].isna().any(axis=1).sum()
            notes = f'{len(latest)} locations in {end}; {missing} without coordinates. {frame.entity.nunique() - len(latest)} historical locations have no row in this quarter. Map and ranking use the same reporting quarter; missing values are never treated as zero. Hospital drill-down follows geographic LHD membership; specialty hospitals without that membership are excluded.'
            if forecast:
                notes = 'Map and ranking show XGBoost forecasts; Δ compares the forecast with the previous historical quarter. Only forecast locations and measures are available. ' + notes
            return pressure_map(latest, boundaries, scope, selected), trend_figure(frame, selected, measure, end, forecast=forecast), table, metrics, str(end), notes
        except (OSError, ValueError, KeyError) as exc:
            return go.Figure(), go.Figure(), dmc.Alert(f'Cannot load trends: {exc}', color='red'), [], 'Unavailable', ''
