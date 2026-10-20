"""Socioeconomic explorer: descriptive LHD aggregates from the Silver layer."""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html
import dash_mantine_components as dmc

SOURCE = Path(__file__).resolve().parents[1] / 'data/silver/linking/lhd_socioeconomic.csv'
INDEXES = {'irsd': 'Relative disadvantage', 'irsad': 'Advantage & disadvantage',
           'ier': 'Economic resources', 'ieo': 'Education & occupation'}
COLORS = ['#d97c53', '#f3e6cd', '#59c2b0', '#117e83']


def load_socioeconomic(path=SOURCE):
    frame = pd.read_csv(path, dtype={'lhd_code': str})
    required = ['lhd_code', 'lhd_name', 'census_year', 'mapped_sa1_count',
                'included_sa1_count', 'excluded_sa1_count', 'population_sum']
    scores = [f'{index}_{method}' for index in INDEXES for method in ('mean', 'population_weighted_mean')]
    if not set(required + scores) <= set(frame.columns) or frame.empty:
        raise ValueError('Socioeconomic file is empty or missing required columns.')
    if frame.lhd_code.isna().any() or frame.lhd_code.duplicated().any() or frame.lhd_name.isna().any():
        raise ValueError('LHD identifiers must be present and unique; names must be present.')
    for col in required[2:] + scores:
        frame[col] = pd.to_numeric(frame[col], errors='raise')
    counts = frame[required[2:]]
    if not np.isfinite(counts).all().all() or (counts < 0).any().any():
        raise ValueError('Years, counts and population must be finite and nonnegative.')
    if not frame.mapped_sa1_count.eq(frame.included_sa1_count + frame.excluded_sa1_count).all():
        raise ValueError('Included and excluded SA1 counts must sum to mapped counts.')
    if np.isinf(frame[scores]).any().any() or (frame[scores] < 0).any().any():
        raise ValueError('Index scores must be nonnegative and finite when present.')
    frame['district'] = frame.lhd_name.str.replace(' Local Health District', '', regex=False)
    frame['coverage'] = frame.included_sa1_count.div(frame.mapped_sa1_count.replace(0, np.nan)).mul(100)
    return frame


def card(children, **kwargs):
    return dmc.Paper(children, p='lg', radius='lg', withBorder=True, **kwargs)


def layout():
    return html.Main([
        html.Div([dmc.Badge('COMMUNITY CONTEXT / ABS SEIFA', color='teal', variant='light'),
                  dmc.Title('Socioeconomic', order=1, mt='md'),
                  dmc.Text('One state. Fifteen districts. Different starting points.', size='xl', mt='xs'),
                  dmc.Text('Explore the socioeconomic landscape behind NSW health districts.', c='dimmed', mt='sm')], className='socio-hero'),
        html.Div(id='socio-metrics', className='metrics'),
        card(dmc.Group([
            dmc.Select(id='socio-index', label='Socioeconomic lens', value='irsd', clearable=False,
                       data=[{'value': k, 'label': f'{k.upper()} · {v}'} for k, v in INDEXES.items()], style={'minWidth': '260px'}),
            dmc.SegmentedControl(id='socio-method', value='population_weighted_mean', data=[
                {'value': 'population_weighted_mean', 'label': 'Population weighted'},
                {'value': 'mean', 'label': 'SA1 mean'}]),
            dmc.Button('Refresh data', id='socio-refresh', variant='light')], justify='space-between'), mt='lg'),
        html.Div(id='socio-error'),
        html.Div([
            card([dmc.Text('THE DISTRICT COMPARISON', size='xs', fw=700, c='teal'),
                  dmc.Title('A spectrum of local context', order=3, mt='xs'),
                  dmc.Text('Lower scores → greater relative disadvantage. Select a dot to explore.', size='sm', c='dimmed'),
                  dcc.Graph(id='socio-ranking', config={'displaylogo': False}, style={'height': '590px'})]),
            card([dmc.Text('DISTRICT SPOTLIGHT', size='xs', fw=700, c='teal'),
                  dmc.Select(id='socio-district', label='Local Health District', searchable=True, clearable=False, mt='md'),
                  html.Div(id='socio-detail')], className='socio-spotlight')], className='socio-grid'),
        card([dmc.Text('THE BIGGER PICTURE', size='xs', fw=700, c='teal'),
              dmc.Title('Four lenses, side by side', order=3, mt='xs'),
              dmc.Text('Each column is coloured relative to its own LHD range. Hover for the actual score; indices measure different concepts.', c='dimmed', size='sm'),
              dcc.Graph(id='socio-heatmap', config={'displaylogo': False}, style={'height': '590px'})], mt='lg'),
        card([dmc.Title('Read the context, respect the limits', order=4),
              dmc.Text('These are descriptive averages of constituent SA1 scores, not official ABS LHD indices, patient characteristics or hospital catchments. Higher scores indicate less disadvantage (IRSD), greater advantage (IRSAD), more economic resources (IER), or higher education and occupational status (IEO).', size='sm', mt='sm'),
              dmc.Text('Population weighting uses the usual resident population of included SA1s. SA1 mean gives each included area equal weight. Only areas with all four scores and population are included; coverage is the share of mapped SA1s included, not population coverage. Entire SA1 populations follow the largest-area LHD assignment. The comparison line is the unweighted mean of available LHD summaries, not an official NSW benchmark.', c='dimmed', size='sm', mt='sm'),
              dmc.Code('data/silver/linking/lhd_socioeconomic.csv', block=True, mt='md')], mt='lg')
    ], id='socio-page', className='main', style={'display': 'none'})


def figures(frame, index, method, selected):
    col = f'{index}_{method}'
    ordered = frame.sort_values(col, ascending=False, na_position='last')
    reference = frame[col].mean()
    rank = go.Figure(go.Scatter(
        x=ordered[col], y=ordered.district, mode='markers',
        customdata=ordered[['lhd_code', 'coverage', 'population_sum']].to_numpy(),
        marker={'size': [17 if code == selected else 12 for code in ordered.lhd_code],
                'color': ordered[col], 'colorscale': COLORS, 'line': {'color': '#fff', 'width': 2}},
        hovertemplate='%{y}<br>Score: %{x:.1f}<br>SA1 coverage: %{customdata[1]:.1f}%<br>Included population: %{customdata[2]:,.0f}<extra></extra>'))
    if pd.notna(reference):
        rank.add_vline(x=reference, line_dash='dot', line_color='#8496a3',
                       annotation_text=f'LHD mean {reference:,.1f}', annotation_position='top')
    rank.update_layout(xaxis_title=f'{index.upper()} · {INDEXES[index]}', yaxis={'autorange': 'reversed'}, clickmode='event+select')
    cols = [f'{key}_{method}' for key in INDEXES]
    raw = ordered[cols]
    scaled = (raw - raw.min()).div((raw.max() - raw.min()).replace(0, np.nan)).fillna(0.5).where(raw.notna())
    heat = go.Figure(go.Heatmap(z=scaled.to_numpy(), x=[key.upper() for key in INDEXES], y=ordered.district,
                              customdata=raw.to_numpy(), text=raw.round(0).to_numpy(), texttemplate='%{text:,.0f}',
                              colorscale=COLORS, zmin=0, zmax=1, xgap=6, ygap=5,
                              colorbar={'title': 'Within-index<br>LHD range', 'tickvals': [0, 1], 'ticktext': ['Lower', 'Higher']},
                              hovertemplate='%{y}<br>%{x}: %{customdata:.1f}<extra></extra>'))
    heat.update_yaxes(autorange='reversed')
    heat.update_xaxes(side='top')
    for fig in (rank, heat):
        fig.update_layout(template='plotly_white', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                          font={'family': 'Inter, system-ui, sans-serif', 'color': '#254456'},
                          margin={'l': 190, 'r': 35, 't': 50, 'b': 55}, showlegend=False)
        fig.update_yaxes(showgrid=False, ticksuffix='  ', automargin=True)
    return rank, heat


def register_callbacks(app):
    @app.callback(Output('socio-district', 'value'), Input('socio-ranking', 'clickData'), prevent_initial_call=True)
    def select(click):
        from dash import no_update
        points = (click or {}).get('points', [])
        return points[0]['customdata'][0] if points and points[0].get('customdata') else no_update

    @app.callback(Output('socio-metrics', 'children'), Output('socio-district', 'data'),
                  Output('socio-ranking', 'figure'), Output('socio-heatmap', 'figure'),
                  Output('socio-detail', 'children'), Output('socio-error', 'children'),
                  Input('socio-index', 'value'), Input('socio-method', 'value'),
                  Input('socio-district', 'value'), Input('socio-refresh', 'n_clicks'))
    def update(index, method, selected, _):
        try:
            frame = load_socioeconomic()
            if index not in INDEXES or method not in {'mean', 'population_weighted_mean'}:
                raise ValueError('Unknown index or averaging method.')
            rank, heat = figures(frame, index, method, selected)
            coverage = 100 * frame.included_sa1_count.sum() / frame.mapped_sa1_count.sum() if frame.mapped_sa1_count.sum() else 0
            stats = [('CENSUS VINTAGE', ' / '.join(str(int(y)) for y in sorted(frame.census_year.unique())), 'SA1 geography and SEIFA'),
                     ('LOCAL HEALTH DISTRICTS', str(len(frame)), 'Geographic district summaries'),
                     ('INCLUDED POPULATION', f'{frame.population_sum.sum() / 1e6:.2f}m', 'Residents in included SA1 areas'),
                     ('SA1 COVERAGE', f'{coverage:.1f}%', f'{int(frame.excluded_sa1_count.sum()):,} mapped areas excluded')]
            metrics = [card([dmc.Text(label, size='xs', fw=700, c='dimmed'), dmc.Title(value, order=2, my='xs'), dmc.Text(note, size='xs', c='dimmed')]) for label, value, note in stats]
            options = [{'value': r.lhd_code, 'label': r.district} for r in frame.sort_values('district').itertuples()]
            rows = frame.loc[frame.lhd_code == selected]
            if rows.empty:
                detail = [dmc.Title('Every district has a story.', order=2, mt='xl'),
                          dmc.Text('Select a district from the chart or menu to see its score, population and data coverage.', c='dimmed', mt='md')]
            else:
                row = rows.iloc[0]
                score = row[f'{index}_{method}']
                detail = [dmc.Title(row.district, order=3, mt='xl'), dmc.Text(index.upper(), size='xs', c='teal', fw=700, mt='xl'),
                          html.Div(f'{score:,.1f}' if pd.notna(score) else 'Unavailable', className='socio-score'),
                          dmc.Text(INDEXES[index], c='dimmed'), dmc.Divider(my='xl'),
                          dmc.Text(f'{row.population_sum:,.0f} included residents', fw=600),
                          dmc.Text(f'{row.included_sa1_count:,.0f} / {row.mapped_sa1_count:,.0f} SA1 areas included', mt='sm'),
                          dmc.Progress(value=float(row.coverage) if pd.notna(row.coverage) else 0, color='teal', mt='sm'),
                          dmc.Text(f'{row.coverage:.1f}% SA1 coverage · {row.excluded_sa1_count:,.0f} excluded', size='sm', c='dimmed', mt='xs')]
            return metrics, options, rank, heat, detail, []
        except (OSError, ValueError, KeyError) as exc:
            return [], [], go.Figure(), go.Figure(), [], dmc.Alert(f'Cannot load socioeconomic data: {exc}', color='red', mt='md')
