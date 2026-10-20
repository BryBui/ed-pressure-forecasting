"""Offline, equal-scale NSW map in Australian Albers kilometres."""
import plotly.graph_objects as go
from data import district_stations


def make_map(data, selected=None, mode="sa1", weather=None):
    fig = go.Figure()
    districts = data['districts']
    for district in districts:
        active = district['code'] == selected
        fig.add_trace(go.Scattergl(x=district['outline_x'], y=district['outline_y'],
            mode='lines', line={'color': '#168c85' if active else '#9eafbb', 'width': 2 if active else 0.8},
            hoverinfo='skip', showlegend=False))
    if selected and mode == "sa1":
        district = next(d for d in districts if d['code'] == selected)
        rows = data['mapping'].loc[data['mapping'].lhd_code == selected]
        x, y = [], []
        for row in rows.itertuples():
            x.extend([row.x, district['x'], None]); y.extend([row.y, district['y'], None])
        fig.add_trace(go.Scattergl(x=x, y=y, mode='lines', name='SA1 → LHD',
            line={'color': 'rgba(22,140,133,0.20)', 'width': 1}, hoverinfo='skip'))
        fig.add_trace(go.Scattergl(x=rows.x, y=rows.y, mode='markers', name='SA1 centres',
            marker={'color': '#168c85', 'size': 3}, text=rows.sa1_code_2021,
            hovertemplate='SA1 %{text}<extra></extra>'))
    if selected and mode == 'weather':
        district = next(d for d in districts if d['code'] == selected)
        rows = district_stations(weather, selected)
        x, y = [], []
        for row in rows.itertuples():
            x.extend([district['x'], row.x, None]); y.extend([district['y'], row.y, None])
        fig.add_trace(go.Scattergl(x=x, y=y, mode='lines', name='LHD → weather station',
            line={'color': 'rgba(209,126,36,0.5)', 'width': 1.5}, hoverinfo='skip'))
        fig.add_trace(go.Scattergl(x=rows.x, y=rows.y, mode='markers', name='Weather stations',
            marker={'color': '#c47b24', 'size': 9, 'symbol': 'diamond'},
            text=[f"{r.name} ({r.bom_station_id})<br>{r.hospital_count} hospital(s): {r.hospitals}<br>Groups: {r.groups}"
                  for r in rows.itertuples()], hovertemplate='%{text}<extra></extra>'))
    fig.add_trace(go.Scattergl(x=[d['x'] for d in districts], y=[d['y'] for d in districts],
        mode='markers', name='LHD centres', customdata=[d['code'] for d in districts],
        text=[d['name'] for d in districts], hovertemplate='%{text}<extra>Click to explore links</extra>',
        marker={'size': [17 if d['code'] == selected else 12 for d in districts],
                'color': ['#f0a94b' if d['code'] == selected else '#153f55' for d in districts],
                'line': {'color': 'white', 'width': 2}}))
    fig.update_layout(template='plotly_white', paper_bgcolor='#ffffff', plot_bgcolor='#f4f8fa',
        margin={'l': 10, 'r': 10, 't': 10, 'b': 10}, height=620, clickmode='event',
        dragmode='pan', uirevision='nsw-reference-map',
        legend={'orientation': 'h', 'y': 0.01, 'x': 0.02, 'bgcolor': 'rgba(255,255,255,0.85)'},
        xaxis={'visible': False}, yaxis={'visible': False, 'scaleanchor': 'x', 'scaleratio': 1})
    return fig


def clicked_lhd(click_data, valid_codes):
    if not click_data or not click_data.get('points'):
        return None
    code = click_data['points'][0].get('customdata')
    return code if isinstance(code, str) and code in valid_codes else None
