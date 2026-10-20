"""Local Data Engineering dashboard. Run from the repository's WSL environment."""
from dash import Dash, Input, Output, State, dcc, html, no_update
import dash_mantine_components as dmc

from data import load_data, load_weather, district_stations
from figures import make_map, clicked_lhd
from socioeconomic import layout as socioeconomic_layout, register_callbacks
from trends import layout as trends_layout, register_callbacks as register_trends_callbacks


def panel(children, **kwargs):
    return dmc.Paper(children, p='lg', radius='md', withBorder=True, **kwargs)


def pipeline(title, schedule, steps, description, source):
    return panel([
        dmc.Group([dmc.Title(title, order=4), dmc.Badge(schedule, variant='light', color='teal')], justify='space-between'),
        html.Div([html.Div([html.Span(f'{i:02}', className='step-number'), html.Span(step)], className='pipeline-step')
                  for i, step in enumerate(steps, 1)], className='pipeline-steps'),
        dmc.Text(description, size='sm', c='dimmed'), dmc.Code(source, block=True, mt='md')])


def create_app():
    app = Dash(__name__, title='Data Engineering | ED Pressure', assets_folder='assets')
    app.layout = dmc.MantineProvider(theme={'primaryColor': 'teal', 'fontFamily': 'Inter, system-ui, sans-serif'}, children=[
        html.Div([
            dcc.Location(id='page-location'),
            html.Aside([html.Div('ED / PRESSURE', className='brand'), html.Div('PROJECT WORKSPACE', className='nav-label'),
                        dcc.Link('◈  Data Engineering', href='/', id='nav-engineering', className='nav-active'),
                        dcc.Link('◉  Socioeconomic', href='/socioeconomic', id='nav-socioeconomic', className='nav-link'),
                        dcc.Link('↗  Current Trends', href='/current-trends', id='nav-trends', className='nav-link'),
                        dcc.Link('◇  Forecasting', href='/forecasting', id='nav-forecast', className='nav-link'),
                        html.P('NSW emergency department\npressure forecasting', className='sidebar-note')], className='sidebar'),
            html.Main([
                dmc.Group([html.Div([dmc.Text('SOURCE → INGEST → REFERENCE', size='xs', fw=700, c='teal'),
                    dmc.Title('Data Engineering', order=1),
                    dmc.Text('Trace the sources, orchestration and geography behind the project.', c='dimmed')]),
                    dmc.Button('Reload local data', id='reload', variant='light')], justify='space-between'),
                dmc.Alert('Repository lineage • local file inventory • Airflow execution status is not connected.',
                          color='blue', mt='lg', mb='lg'),
                html.Div(id='inventory', className='metrics'),
                dmc.Title('Geographic correspondence', order=2, mt='xl', mb='xs'),
                dmc.Text('Choose a link type, then select an LHD circle. Pan and zoom to inspect dense areas.', c='dimmed', mb='md'),
                dmc.SegmentedControl(id='map-mode', value='sa1', data=[
                    {'label': 'SA1 areas', 'value': 'sa1'},
                    {'label': 'Weather stations', 'value': 'weather'}], mb='md'),
                html.Div([
                    panel(dcc.Loading(dcc.Graph(id='nsw-map', config={'displaylogo': False, 'scrollZoom': True},
                                                style={'height': '620px'}))),
                    panel([dmc.Text('EXPLORE A DISTRICT', size='xs', fw=700, c='teal'),
                        dmc.Select(id='district', label='Local Health District', placeholder='Choose a district',
                                   searchable=True, clearable=True, data=[], mt='md'),
                        html.Div(id='district-details'),
                        dmc.Divider(my='lg'), dmc.Text('How the links are built', fw=600),
                        html.Div(id='map-method')])], className='map-grid'),
                dmc.Title('Apache Airflow orchestration', order=2, mt='xl'),
                dmc.Text('Schedules below are UTC and describe the checked-in DAG definitions, not live run results.', c='dimmed', mb='md'),
                html.Div([
                    pipeline('BHI · Emergency department performance', 'Monthly · day 1 · 09:00 UTC',
                        ['Check published workbook', 'Save only new content', 'Gate: continue_if_new', 'Trigger build_reference_data'],
                        'BHI supplies ED performance workbooks. An unchanged workbook skips the reference trigger. New content is stored as an immutable raw snapshot.', 'airflow/dags/ingest_bhi.py'),
                    pipeline('Reference · Identities and geography', 'Triggered · no schedule',
                        ['Extract current BHI LHDs and hospitals', 'Validate identities and LHD boundaries', 'Write validated reference artifacts'],
                        'build_reference_data validates the 15 geographic LHDs and current BHI hospital identities. Approved weather mappings are preserved during reference refreshes.', 'airflow/dags/build_reference_data.py'),
                    pipeline('BoM · Weather observations', 'Daily · 09:00 UTC',
                        ['Read approved hospital–station mapping', 'Snapshot NSW station catalogue', 'Collect rolling 72-hour observations'],
                        'The public feed is collected daily with intentional overlap. Unsupported stations are reported explicitly. Deduplication and monthly aggregation belong to later Silver processing; these are not yet quality-controlled historical observations.', 'airflow/dags/ingest_bom.py'),
                    pipeline('ABS · SEIFA and SA1 boundaries', 'Daily · 09:00 UTC',
                        ['Check latest SEIFA reference period', 'Retain new Census-year workbook', 'Fetch matching Census-year SA1 shapefile'],
                        'The boundary task follows the SEIFA result, including when the workbook already exists. The Census year selects the matching boundary archive; validated existing archives are reused.', 'airflow/dags/ingest_seifa.py')], className='pipeline-grid'),
                panel([dmc.Title('From raw snapshots to this map', order=3),
                    dmc.Text('Official 2021 ABS SA1 polygons + NSW Health LHD polygons → validate and project to EPSG:3577 → intersect polygons → choose largest-area LHD → correspondence CSV.', mt='sm'),
                    dmc.Text('SA1-to-LHD mapping is a separate reference-processing command; it is not a task in the four DAGs above. Raw files remain unchanged. Silver standardisation and Gold analytical outputs are later layers.', c='dimmed', size='sm', mt='sm'),
                    dmc.Code('.venv-wsl/bin/python -m reference.SA1_to_LHD --sa1 data/reference/nsw_sa1_boundaries.geojson', block=True, mt='md')], mt='xl'),
                dmc.Text('LOCAL REFERENCE EXPLORER · No source-system requests or raw-data writes', size='xs', c='dimmed', mt='xl')
            ], id='engineering-page', className='main'), socioeconomic_layout(), trends_layout(), trends_layout(forecast=True)], className='shell')])

    register_callbacks(app)
    register_trends_callbacks(app)
    register_trends_callbacks(app, forecast=True)

    @app.callback(Output('engineering-page', 'style'), Output('socio-page', 'style'),
                  Output('nav-engineering', 'className'), Output('nav-socioeconomic', 'className'),
                  Output('trends-page', 'style'), Output('nav-trends', 'className'),
                  Output('forecast-page', 'style'), Output('nav-forecast', 'className'),
                  Input('page-location', 'pathname'))
    def route(path):
        socio = (path or '/').rstrip('/') == '/socioeconomic'
        trends = (path or '/').rstrip('/') == '/current-trends'
        forecast = (path or '/').rstrip('/') == '/forecasting'
        return ({'display': 'none'} if socio or trends or forecast else {}, {} if socio else {'display': 'none'},
                'nav-link' if socio or trends or forecast else 'nav-active', 'nav-active' if socio else 'nav-link',
                {} if trends else {'display': 'none'}, 'nav-active' if trends else 'nav-link',
                {} if forecast else {'display': 'none'}, 'nav-active' if forecast else 'nav-link')

    @app.callback(Output('district', 'data'), Output('inventory', 'children'), Input('reload', 'n_clicks'))
    def inventory(_):
        try:
            data = load_data()
            stats = [('SOURCE FAMILIES', '3', 'BHI · BoM · ABS'),
                     ('GEOGRAPHIC LHDS', str(len(data['districts'])), 'Official NSW Health boundaries'),
                     ('MAPPED SA1 AREAS', f"{len(data['mapping']):,}", 'CSV assignment records'),
                     ('EXCLUDED SA1 AREAS', str(data['excluded']) if data['excluded'] is not None else 'Unknown', 'Exclusion report on disk')]
            return ([{'value': d['code'], 'label': d['name']} for d in sorted(data['districts'], key=lambda d: d['name'])],
                    [panel([dmc.Text(label, size='xs', c='dimmed', fw=700), dmc.Title(value, order=2, my='xs'), dmc.Text(note, size='xs', c='dimmed')]) for label, value, note in stats])
        except (OSError, ValueError, KeyError) as exc:
            return [], dmc.Alert(f'Cannot load reference data: {exc}', color='red')

    @app.callback(Output('district', 'value'), Input('nsw-map', 'clickData'), State('district', 'data'), prevent_initial_call=True)
    def select_district(click_data, options):
        return clicked_lhd(click_data, {o['value'] for o in options}) or no_update

    @app.callback(Output('nsw-map', 'figure'), Output('district-details', 'children'),
                  Output('map-method', 'children'),
                  Input('district', 'value'), Input('reload', 'n_clicks'), Input('map-mode', 'value'))
    def update_map(selected, _, mode):
        try:
            data = load_data()
            if selected not in {d['code'] for d in data['districts']}:
                selected = None
            weather = load_weather() if mode == 'weather' else None
            if weather is not None and not set(weather['rows'].boundary_code) <= {d['code'] for d in data['districts']}:
                raise ValueError('Weather assignments contain LHD codes absent from the map.')
            method = ([
                dmc.Text('Approved hospital–station assignments are grouped by geographic LHD using nsw_lhds.csv. One line connects each distinct station to the LHD centre; hover for hospitals and observation groups.', size='sm', c='dimmed', mt='sm'),
                dmc.Text('Stations are placed using their GeoJSON coordinates. Lines summarise hospital assignments, not distance from a hospital or a new station-selection rule.', size='sm', c='dimmed', mt='sm'),
                dmc.Text(f"{weather['unassigned_hospitals']} hospital(s) have no geographic LHD and are not linked. {weather['unapproved_rows']} unapproved mapping rows are omitted.", size='sm', mt='sm'),
                *[dmc.Code(name, block=True, mt='xs') for name in ['hospital_weather_mapping.csv', 'weather_stations.geojson', 'nsw_lhds.csv']]
            ] if mode == 'weather' else [
                dmc.Text('SA1-to-LHD links were created using GeoPandas to intersect the boundary polygons. The CSV determines membership. Centres are calculated from the original polygons in Australian Albers (EPSG:3577). Cross-boundary SA1s use the largest intersecting area.', size='sm', c='dimmed', mt='sm'),
                dmc.Text('These links are geographic assignments, not patient flows or hospital catchments.', size='sm', c='dimmed', mt='sm'),
                dmc.Code('sa1_to_lhd.csv', block=True, mt='md'),
                dmc.Code('nsw_sa1_boundaries.geojson', block=True, mt='xs')])
            figure = make_map(data, selected, mode, weather)
            if not selected:
                return figure, dmc.Text('Choose a circle or use the searchable selector above. All LHDs remain visible.', c='dimmed', mt='lg'), method
            if mode == 'weather':
                stations = district_stations(weather, selected)
                rows = weather['rows'].loc[weather['rows'].boundary_code == selected]
                district = next(d for d in data['districts'] if d['code'] == selected)
                return figure, html.Div([dmc.Title(district['name'], order=3, mt='lg'),
                    dmc.Title(f'{len(stations):,}', order=1, mt='md'),
                    dmc.Text('Distinct weather stations', c='dimmed'),
                    dmc.Text(f"{rows.bhi_hospital_id.nunique()} hospitals · {len(rows)} approved hospital–observation assignments", size='sm', mt='md'),
                    dmc.Text('No approved stations linked to this LHD.' if rows.empty else 'Hover over station diamonds to inspect their provenance.', size='sm', mt='sm')]), method
            rows = data['mapping'].loc[data['mapping'].lhd_code == selected]
            district = next(d for d in data['districts'] if d['code'] == selected)
            return figure, html.Div([dmc.Title(district['name'], order=3, mt='lg'),
                dmc.Title(f'{len(rows):,}', order=1, mt='md'), dmc.Text('SA1 areas linked to this LHD', c='dimmed'),
                dmc.Text(f"{(rows.overlap_pct < 99.999).sum():,} assignments with overlap below 100%", size='sm', mt='md'),
                dmc.Text(f"Assignment methods: {', '.join(sorted(rows.assignment_method.unique()))}", size='sm', mt='sm')]), method
        except (OSError, ValueError, KeyError) as exc:
            return {}, dmc.Alert(f'Cannot build map: {exc}', color='red', mt='lg'), []
    return app


app = create_app()
server = app.server

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8050, debug=False)
