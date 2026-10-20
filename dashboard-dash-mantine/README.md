# Data Engineering dashboard

Run from the repository root with the project WSL environment:

```bash
.venv-wsl/bin/python -m pip install -e .
.venv-wsl/bin/python -m pip install -r dashboard-dash-mantine/requirements.txt
.venv-wsl/bin/python dashboard-dash-mantine/app.py
```

Open http://127.0.0.1:8050. The server binds to localhost with debugging disabled.

The dashboard explains the four checked-in Airflow DAGs; it does not connect to
Airflow or imply that a scheduled run has succeeded. Update the explanatory cards
when DAG definitions change.

The map reads these existing files without modifying them:

- `data/reference/nsw_lhd_boundaries.geojson`
- `data/reference/nsw_sa1_boundaries.geojson`
- `data/reference/sa1_to_lhd.csv`
- `data/reference/sa1_to_lhd_excluded.csv` (optional exclusion count)

Select any LHD circle or use the searchable selector. Clear the selector to hide
links. Every assigned SA1 is shown, without sampling. Pan/zoom for Sydney's dense
clusters; clearing a selection preserves the map viewport. Double-click to reset
the view. Reload local data after reference regeneration, then reselect a district.

Centres are area-weighted polygon centroids calculated in Australian Albers
(EPSG:3577); centroids can lie outside concave or multipart polygons. The equal-scale
map uses projected kilometres, no remote basemap or map token. LHD polygons depict
NSW geographic health-district coverage, not a separate state administrative
boundary. Display outlines are simplified by 250 metres; centroid calculations and
CSV memberships use the original data. Lines show assignments, not patient flows.

First load parses roughly 278 MB of geometry and may take several seconds. Geometry
and joined centres are cached in server memory, invalidated by input modification
times. Each worker maintains its own cache. Only simplified outlines and selected
centres reach the browser. Restart after changing the optional exclusions file.
Missing inputs or inconsistent mapped codes produce a visible error.

Tests (from repository root):

```bash
.venv-wsl/bin/python -m unittest discover -s dashboard-dash-mantine -p 'test_*.py' -v
.venv-wsl/bin/python -m unittest discover -s tests -v
```


## Weather station option

Switch the map to **Weather stations**, then select an LHD using its circle or
the existing selector. The district selection and viewport persist across modes.
One orange line is drawn to each distinct station used by that LHD's hospitals;
hover over a station diamond for its station ID/name, hospital names, and weather
observation groups. The sidebar counts distinct stations, hospitals and approved
hospital–observation assignments.

`hospital_weather_mapping.csv` supplies approved assignments and geographic BHI LHD
IDs. `nsw_lhds.csv` bridges those IDs to the boundary codes used on the map.
`weather_stations.geojson` supplies station coordinates, projected to EPSG:3577.
A shared station is drawn once per selected LHD even when used by multiple
hospitals or observation groups. Station placement does not use CSV coordinates.
Stations can fall outside their associated LHD or NSW. The view can be panned and
zoomed to inspect them. These are summaries of hospital assignments, not new
station selections based on the LHD centre.

Hospitals without geographic LHD IDs (including specialty networks) are counted
but not assigned by location. Unapproved rows are omitted and counted. Unknown
LHD IDs and missing station geometry produce visible errors in weather mode;
they do not prevent use of the SA1 option. Weather inputs are cached separately
and checked for modification on each callback, including Reload local data.

## Socioeconomic page

Open `/socioeconomic` or choose **Socioeconomic** in the sidebar. This page reads
`data/silver/linking/lhd_socioeconomic.csv` independently of the geographic map.
Select any of the four SEIFA indices, switch between population-weighted and
SA1 means, and click a ranking dot or choose a district to inspect its profile.
The heatmap shows actual scores and colours each index by its own LHD range.
The dotted comparison line is the unweighted mean of available LHD summaries.
Coverage counts included SA1s, not residents. These are descriptive aggregates,
not official ABS LHD indices or patient-level measures. **Refresh data** rereads
the CSV; missing or invalid data produces a visible error without changing files.


### Current Trends

Open `/current-trends` from the sidebar. All LHDs shows the LHD quarterly panel;
selecting a district shows hospitals linked by `geographic_lhd_name` in the
hospital reference. The two sources are `data/silver/linking/lhd_bhi_lags.csv`
and `hospital_bhi_lags.csv`. Hospital points use the existing hospital coordinates
in `hospital_weather_mapping.csv`; LHD centres use original Albers polygons.

The map and ranking use the latest quarter in the selected data level, without
carrying older observations forward. Circle area and colour encode the stored
0–1 pressure index. Click a circle or use the selector below the map to explore
12 calendar quarters. The ED measure selector includes all available panel
measures in their original units. The trailing four-quarter mean requires four
consecutive reported values and can use history before the visible window.
The ranking shows quarter-on-quarter index differences; positive means more
pressure. Missing data remain missing. Specialty hospitals without a geographic
LHD are outside district drill-downs. Local inputs are cached by modification
time and checked again when refreshed.

Verify with `.venv-wsl/bin/python -m unittest discover -s dashboard-dash-mantine -v`
from the repository root.


### Forecasting

Open `/forecasting` or choose **Forecasting** beneath Current Trends. The page
shares the Current Trends layout and geographic drill-down. Map, ranking, and
summary cards use `data/gold/xgboost/{LHDs,Hospitals}/next_quarter_predictions.csv`.
Quarter-on-quarter differences compare predictions with the previous historical
quarter. The chart shows 11 historical calendar quarters plus the forecast,
marked by a diamond and dashed segment; missing historical values remain gaps.

Measures are constrained programmatically to the intersection of `targets` in
the selected level's `training_hyperparameters.json` and its prediction CSV
columns. Historical-only measures and model input features are excluded.
Refresh rereads predictions and configuration. Inputs remain unchanged.
