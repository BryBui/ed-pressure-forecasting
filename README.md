# Healthcare Data System

This repository separates source ingestion, geographic reference preparation,
and later Silver/Gold transformations:

```text
airflow/dags/           Airflow orchestration
src/ingestion/          Source-specific ingestion code
src/reference/          LHD/hospital extraction, validation, and mapping
src/transform/bhi/      BHI workbook export, quarterly panels, and pressure index
src/transform/socioeconomic/  SA1 SEIFA aggregation for LHDs and hospitals
data/raw/               Immutable source snapshots
data/reference/         Validated identities, boundaries, and weather mapping
data/silver/            Standardised data
data/gold/              Analytical outputs
tests/                  Offline unit tests
```

Transform scripts run as modules from the project root:

```bash
.venv-wsl/bin/python -m transform.bhi.bhi_ed_to_csv
.venv-wsl/bin/python -m transform.bhi.hospital_bhi_ed_lags
.venv-wsl/bin/python -m transform.bhi.lhd_bhi_ed_lags
.venv-wsl/bin/python -m transform.socioeconomic.lhd_socioeconomic
.venv-wsl/bin/python -m transform.socioeconomic.hospital_socioeconomic
```

Use `--help` for input and output options. The BHI pressure weights live in
`src/transform/bhi/pressure_indices.toml`, alongside the shared calculation.
The historical `*_lags` filenames currently build current-quarter panels.

## BHI reference-data flow

The current flow is:

```text
BHI release watcher
  -> immutable BHI workbook
  -> extract BHI reporting_level=LHD rows
  -> exclude non-geographic specialty networks
  -> validate 15 LHDs against NSW Health boundary data
  -> extract hospitals present in BHI's latest quarter, preserving BHI IDs
  -> validate identities, history, and parent relationships
  -> nsw_lhds.csv + nsw_hospitals.csv + validation report
  -> official point coordinates for the 73 current hospitals
  -> approved feature-specific hospital-to-BoM station mapping
```

`airflow/dags/ingest_bhi.py` checks BHI's public download page at 09:00 UTC on
the first day of each month. The polling task creates a raw snapshot only when
workbook content is new. A new snapshot triggers the otherwise-unscheduled
`build_reference_data` DAG; unchanged checks do not rebuild the reference.

The reference code reads BHI's `Overall results` sheet. BHI reports specialty
networks at the `LHD` reporting level, so only entity IDs ending in `LHD` are
treated as geographic districts. The resulting 15 names must match the current
official NSW boundary service one-to-one.

Generated reference artifacts:

- `data/reference/nsw_lhds.csv`: BHI IDs/names joined to official NSW boundary
  codes and names.
- `data/reference/nsw_lhd_boundaries.geojson`: official WGS84 polygons retained
  for station selection and spatial validation.
- `data/reference/nsw_hospitals.csv`: hospitals present in BHI's latest ED
  quarter, including BHI IDs, parent organisation, and reporting history.
- `data/reference/hospital_weather_mapping.csv`: five rows per BHI hospital,
  one for each weather observation group. Each row contains an official NSW
  hospital point and an approved BoM station with coverage and distance
  metadata. Existing role-aware approved rows survive reference refreshes.
- `data/reference/weather_stations.geojson`: one WGS84 point per mapped BoM
  station, with its ID, name, observation roles, linked hospital IDs, and
  hospital count. It is refreshed from the final mapping CSV whenever the
  hospital/weather mapping is written. Pending rows without station
  coordinates are omitted; shared stations appear once.
- `data/reference/reference_validation_report.json`: counts, scope, warnings,
  and whether an authoritative statewide hospital register was checked.

The hospital reference is deliberately not described as a complete NSW
hospital register. It contains only current hospitals in this BHI ED dataset;
historical-only hospitals remain reproducible from the immutable raw workbook
but are excluded from reference and weather mapping outputs. Three current
hospitals report through specialty networks rather than geographic LHDs. Their
BHI relationships are retained, their LHD fields remain blank by design, and
their official point coordinates drive weather-station selection.

## BoM mapping gate

One station at an arbitrary LHD centroid is not a safe default: several rural
LHDs span large and climatically diverse areas. Hospital coordinates are
matched by exact official name where possible; reviewed aliases handle
different BHI/MyHospitals names, and the current NSW spatial layer supplies the
renamed Tweed Valley Hospital.

MyHospitals is used only as supporting geographic reference data. Its annual ED
performance datasets are intentionally excluded from ingestion and forecasting
because they are too coarse and substantially overlap BHI for the current
scope.

The approved hybrid policy has five roles: temperature, rainfall, relative
humidity, wind/gust, and thunderstorms. The nearest current station satisfying
the role's official BoM holding lists is selected. One station is reused where
it covers multiple roles; a role-specific fallback is used otherwise. BoM's
dew-point holding is the availability proxy for relative humidity, while total
cloud is its documented proxy for visual observations such as thunder.

Historical holding completeness is retained as review metadata. Locality is
prioritised for current observations, and actual completeness must be checked
for every ingested month. Thunder selections beyond the preferred 50 km
distance are retained as `regional_proxy` mappings. Temperature climatology
for Excess Heat Factor is a separate versioned input; it must not force current
temperature observations to a distant station.

`airflow/dags/ingest_bom.py` runs daily at 09:00 UTC. The public Climate Data
Online pages do not support automated scraping, so the DAG uses BoM's official
public observation feeds instead. It snapshots the NSW station catalogue and
the rolling 72-hour JSON product for every approved mapped station advertised
by that catalogue. The overlap is intentional: later processing deduplicates
records by station and `aifstime_utc`, while a short failed run does not create
a gap. Identical retries do not create duplicate raw files.

The public feed does not contain every climate station and is real-time rather
than a quality-controlled historical archive. Unsupported approved station IDs
are returned explicitly in the Airflow task result; the ingestion does not
silently substitute another station. Consequently this path cannot backfill
data from before the collector started, and public-feed values must retain
BoM's warning that routine quality checks may not yet have been performed.

Daily polling of the rolling observation feed remains the selected ingestion
strategy. Because the station endpoint returns all observation elements in one
payload, Bronze retains the complete response. Silver processing will
deduplicate the overlapping snapshots, derive daily measures, and then produce
the required monthly aggregates. Completed monthly DWO products are not a
routine ingestion dependency.

BoM's national summary is generally published on the first working day and
regional summaries on the third. The release-window helper is retained for the
later completed-month aggregation gate, but the rolling public observations
must be collected daily rather than waiting until month-end.

The station-quarter export can be generated directly from these raw snapshots:

```bash
.venv-wsl/bin/python -m transform.bom.bom_to_quarters
```

It writes `data/silver/bom/station_quarters.csv`, with one row per station and
local calendar quarter. Overlapping observations are deduplicated by station
and UTC timestamp, preferring the latest snapshot. Temperature, apparent
temperature, dew point, humidity, wind speed, gust and sea-level pressure have
minimum, mean, maximum and valid observation counts. These describe available
subdaily readings, not daily or monthly means. Missing measurements stay blank.
Coverage columns report first/last local timestamps, observed days, expected
days and an incomplete-quarter flag (at least one calendar day is absent).
Having observations on every day does not guarantee complete intraday coverage.
Rainfall is exported only as the maximum available cumulative since-9am reading
(`rain_since_9am_max_mm`), not a quarterly rainfall total. Routine public-feed
observations may not yet have undergone BoM quality checks.
Use `--input-dir` and `--output` to override the default paths.

## BoM daily-to-monthly feature design

The requested health-relevant measures require daily source observations, even
though ingestion remains partitioned by completed month:

```text
data/raw/bom/daily_weather_observations/year=YYYY/month=MM/
```

The feasible public-feed raw inputs are stored without transformation as:

```text
data/raw/bom/nsw_observation_catalog/year=YYYY/month=MM/*.xml
data/raw/bom/station_observations/year=YYYY/month=MM/station_id=NNNNNN/*.json
```

Monthly Silver features are derived as follows:

- temperature: minimum, mean, and maximum;
- extreme heat days: daily maximum temperature at or above 35 degrees Celsius;
- cold days: daily maximum temperature at or below 10 degrees Celsius;
- cold nights: daily minimum temperature at or below 5 degrees Celsius;
- heatwaves: event/day counts and minimum, mean, and maximum event duration,
  using BoM's Excess Heat Factor rather than a fixed monthly threshold;
- rainfall: daily minimum, mean, maximum, monthly total, and rain-day count;
- relative humidity: minimum, mean, and maximum across available 9am/3pm data;
- wind: minimum, mean, and maximum routine speed and maximum-gust series;
- thunderstorms: count of days with recorded thunder.

These screening thresholds are explicit in the ingestion feature contract.
Thunder and some other elements are station-dependent, so station selection
must validate element coverage before approval.

## Raw snapshot contract

Raw files remain source-first and immutable:

```text
data/raw/{source}/{dataset}/{partitions...}/
  {dataset}_{reporting_period}_{ingestion_timestamp}.{extension}
```

BHI workbooks are stored as:

```text
data/raw/bhi/ed_performance/year=YYYY/quarter=QX/
  ed_performance_YYYYQX_YYYYMMDDTHHMMSSZ.xlsx
```

Retries reuse the Airflow run timestamp. A later check with identical content
does not create a duplicate, while a revised workbook creates another snapshot.

## ABS SEIFA ingestion

SEIFA ingestion is scoped to the official Statistical Area Level 1 (SA1)
indexes workbook. The complete workbook is retained so IRSD, IRSAD, IER, IEO,
excluded-area flags, and geographic reference fields remain available for
later transformation.

The ingestion module accepts an official ABS release page or direct workbook
URL, validates the Census year and SA1 workbook structure, and stores
hash-deduplicated snapshots as:

```text
data/raw/abs/seifa/census_year=YYYY/
  seifa_YYYY_YYYYMMDDTHHMMSSZ.xlsx
```

The `ingest_seifa` DAG checks the official ABS latest-release page daily at
09:00 UTC. It requires the `Latest release` marker, reads the four-digit
reference period, and confirms that period matches the linked SA1 workbook.
The workbook is downloaded only when that Census-year partition has no stored
snapshot. A changed or missing marker fails the task visibly because it may
indicate that ABS changed the page contract. SEIFA must eventually be joined
through patient residence or a defined hospital catchment; the SA1 containing
the hospital building is not a patient socioeconomic measure.

The same DAG then downloads the matching Census year's official national
GDA2020 SA1 shapefile ZIP using `ingestion.SA1_shapefile`. It retains the
original archive, including the `.shp`, `.shx`, `.dbf`, and `.prj` components:

```text
data/raw/abs/SA1/census_year=YYYY/SA1_YYYY_YYYYMMDDTHHMMSSZ.zip
```

The boundary task validates ZIP integrity and required components before
publishing the snapshot. Existing validated boundaries are reused without a
network request. The task also runs when SEIFA is already stored, allowing
missing boundaries to be backfilled and failed boundary downloads to be
retried on the next run. Geography is selected from the SEIFA reference year,
not from the latest available ASGS release. ASGS edition URLs currently follow
five-year intervals from 2021; a changed ABS naming scheme fails discovery
rather than silently selecting another year's boundaries.

Download boundaries independently with:

```bash
.venv-wsl/bin/python -m ingestion.SA1_shapefile --census-year 2021
```

NSW filtering and SA1-to-LHD assignment remain separate reference processing.
The mapper accepts the downloaded ZIP directly, an extracted `.shp`, or
GeoJSON. ZIPs must contain exactly one shapefile; raw files are not extracted
or modified. For example:

```bash
.venv-wsl/bin/python -m reference.SA1_to_LHD \
  --sa1 data/raw/abs/SA1/census_year=2021/SA1_2021_20261020T025040Z.zip
```

The four known nonspatial NSW 2021 records (migratory/offshore/shipping and
no usual address) have no polygon and are excluded with a warning. Missing
geometry in other SA1 records still fails validation.
SA1 polygons with no positive-area overlap with any LHD are excluded from the
CSV with a warning listing every excluded code. Edge-only contact is not an
area overlap. Partially overlapping polygons retain their largest-area LHD
assignment and overlap percentage.
Excluded codes are also written to `data/reference/sa1_to_lhd_excluded.csv`
by default, with `sa1_code_2021` and `exclusion_reason` columns. Reasons are
`no_positive_area_lhd_overlap` or `nonspatial_record`. Use `--excluded-output`
to select a different location; otherwise the exclusions CSV follows the
mapping filename as `<output stem>_excluded.csv`. Even when nothing is
excluded, the file is refreshed with its header so stale exclusions are not
retained from an earlier run.
The mapper also exports `data/reference/nsw_sa1_boundaries.geojson` for reuse
and visualisation. It contains all NSW SA1 polygons in WGS84 (`EPSG:4326`)
with the original ABS attributes, including polygons excluded from LHD
assignment because they have no overlap. Nonspatial records are omitted.
Use `--boundaries-output` to override the location; by default the GeoJSON
is written beside the mapping CSV. The raw ZIP is read directly and preserved.

## Installation and tests

Install the source package into the Airflow environment:

```bash
pip install -e .
```

Explain every ingestion path without contacting a source system:

```bash
.venv-wsl/bin/python -m ingestion.demo_ingest --explain-only
```

Run the live duplicate-safe demonstration for all available sources, or select
one with `--source bhi`, `--source bom`, or `--source seifa`:

```bash
.venv-wsl/bin/python -m ingestion.demo_ingest
```

The manual helper lives in `src/ingestion/demo_ingest.py`, alongside the ingestion
modules it calls. It runs independently of Airflow and the dashboard. Use
`--raw-root`, `--mapping`, and `--hospitals` to override its repository defaults.
Its SEIFA check downloads the workbook; matching boundary ingestion remains a
separate step in the SEIFA Airflow DAG.

The demonstration prints each source URL, ingestion status, and the absolute
location of every file or output directory when it finishes.

Airflow 3 should be installed with its official constraints, together with the
standard provider used for cross-DAG triggering. On Windows, use WSL2 or a
Linux container for Airflow itself.

Run the offline unit suite with:

```bash
python -m unittest discover -s tests -v
```

Deployment validation should also include:

```bash
airflow dags list-import-errors
airflow dags test ingest_bhi 2026-10-18
airflow tasks test ingest_seifa ingest_if_new_period
```
