# Healthcare Data System — Decision Log

This document records the architectural, data-scope, ingestion, reference-data,
weather-mapping, and operational decisions made as of 19 October 2026. It is
the durable companion to the implementation and should be updated when a
decision changes.

## 1. Repository and layer structure

### Decision

Use the following separation of responsibilities:

- `airflow/dags/`: orchestration only;
- `src/ingestion/`: source discovery, validation, download, and raw storage;
- `src/reference/`: LHD/hospital extraction, validation, and geographic mapping;
- `data/raw/`: immutable source snapshots;
- `data/reference/`: validated reusable reference datasets;
- `data/silver/`: standardised daily/monthly data and cross-source joins;
- `data/gold/`: analytical and forecasting outputs;
- `tests/`: offline unit and integration-style tests with source fixtures;
- `notebooks/`: exploration, not production pipeline logic.

### Rationale and consequence

Airflow DAGs remain thin and testable business logic remains importable without
Airflow. Raw source fidelity is separated from Silver standardisation and Gold
analysis.

## 2. Raw-layer storage contract

### Decision

Raw files use a source-first, reporting-period-partitioned layout:

```text
data/raw/{source}/{dataset}/{partitions...}/
  {dataset}_{reporting_period}_{ingestion_timestamp}.{extension}
```

- Ingestion timestamps are UTC and formatted `YYYYMMDDTHHMMSSZ`.
- Source formats are retained where technically possible.
- Existing raw files are never overwritten.
- Identical content is detected by SHA-256 and is not stored twice.
- An Airflow retry reuses its run timestamp and is idempotent.
- A later, genuinely revised source payload creates a new snapshot.
- Files are downloaded completely and then published atomically.

### Rationale and consequence

This supports auditability, correction detection, reproducibility, and a later
migration to object storage such as Azure Data Lake Storage.

## 3. Publication-aware ingestion

### Decision

Pipelines poll for source state and inspect content rather than assuming that a
new release exists solely because a calendar date has arrived.

- BHI is checked at 09:00 UTC on the first day of each month. Its dataset is
  quarterly, and a daily full-workbook download is not proportionate because
  the server does not publish a hash, ETag, or Last-Modified validator.
- BoM's feasible public feed is collected daily at 09:00 UTC because it is a
  rolling 72-hour product, not a month-end archive.
- `catchup=False` and `max_active_runs=1` are used for release watchers.
- ABS SEIFA checks the official latest-release page daily at 09:00 UTC. It
  downloads the SA1 workbook only when the advertised reference period has no
  stored snapshot.

### BoM publication calendar

- National monthly summaries are generally published on the first working day
  of the next month, based on data available at 15:00 that day.
- Regional and capital-city summaries are generally published on the third
  working day of the next month.
- The implementation treats a working day as Monday to Friday and calculates
  the boundary in `Australia/Sydney`, including daylight saving.
- The third-working-day boundary is the conservative earliest gate for a later
  completed-month aggregation. It is not proof that every station observation
  is complete.

## 4. BHI source scope and ingestion

### Decision

- Scope BHI ingestion strictly to the Emergency Department downloadable
  workbook.
- Discover the currently advertised workbook from BHI's download page instead
  of hard-coding a quarterly URL.
- Derive the reporting year and quarter from the official workbook filename.
- Store each workbook under:

```text
data/raw/bhi/ed_performance/year=YYYY/quarter=QX/
  ed_performance_YYYYQX_YYYYMMDDTHHMMSSZ.xlsx
```

- A new BHI snapshot triggers the otherwise-unscheduled
  `build_reference_data` DAG. An unchanged poll does not rebuild references.

### Current implementation state

The current raw snapshot represents BHI ED 2026 Q2. Publication dates are not
inferred beyond what is encoded in the advertised workbook.

## 5. Hospital population

### Decision

- Analyse only hospitals present in the latest reporting quarter of the BHI ED
  dataset.
- Preserve BHI's official hospital identifiers wherever supplied.
- Do not add NSW hospitals that do not occur in this BHI ED scope.
- Preserve historical reporting metadata, but exclude hospitals absent from
  the latest quarter from the active reference and weather mapping.
- Do not claim that `nsw_hospitals.csv` is a complete NSW hospital register.

### Current validated population

- 80 unique hospitals appear historically in the source workbook.
- 73 are present in the latest quarter and are included.
- 7 historical-only hospitals are excluded from the active reference.
- 70 current hospitals belong to geographic LHDs.
- 3 current hospitals report through specialty networks.

### Rationale and consequence

The population matches the requested analytical cohort. A separate
authoritative statewide register comparison is unnecessary unless the scope is
later expanded beyond BHI ED.

## 6. LHD extraction and validation

### Decision

- Read the BHI workbook's `Overall results` sheet.
- Extract rows where `reporting_level` is `LHD`.
- Treat only BHI entity IDs ending in `LHD` as geographic Local Health
  Districts; BHI specialty networks reported at the same level are excluded.
- Require the resulting 15 geographic LHDs to match the official NSW Health
  boundary service one-to-one.
- Retain official WGS84 LHD polygons as reference data.

### Outputs

- `data/reference/nsw_lhds.csv`
- `data/reference/nsw_lhd_boundaries.geojson`

## 7. Hospital identity and coordinates

### Decision

- BHI identifiers and parent relationships remain the analytical identity
  contract.
- Hospital coordinates come from official NSW point datasets, not an LHD
  centroid.
- Match exact normalised names first.
- Use an explicit reviewed alias table where BHI and MyHospitals names differ.
- Resolve Tweed Valley Hospital through the current NSW spatial health-facility
  service because its current identity is not available through the same
  MyHospitals match path.
- Specialty-network hospitals retain their BHI parent; their geographic LHD
  fields remain blank, and their hospital point coordinates are used directly
  for weather mapping.

### Current coordinate resolution

- 54 hospitals matched MyHospitals directly.
- 18 hospitals use reviewed BHI-to-MyHospitals aliases.
- Tweed Valley Hospital uses the current NSW spatial record.

## 8. Hospital-to-weather mapping unit

### Decision

Map weather at hospital level rather than assigning one station to an entire
LHD. Large and climatically diverse LHDs make centroid-level assignment too
coarse for ED-pressure modelling.

Each of the 73 hospitals has one approved mapping for each of five observation
roles, producing 365 mapping rows:

| Role | BoM holding elements | Minimum record | Minimum completeness | Preferred distance |
| --- | --- | ---: | ---: | ---: |
| Temperature | 122 maximum + 123 minimum temperature | 1 year | 50% | 50 km |
| Rainfall | 136 daily rainfall | 1 year | 50% | 50 km |
| Relative humidity | 5 dew point as availability proxy | 1 year | 50% | 75 km |
| Wind/gust | 7 wind speed + 131 maximum gust | 1 year | 50% | 75 km |
| Thunderstorms | 12 total cloud as visual-observation proxy | 5 years | 50% | 50 km |

- Select the nearest current station satisfying all holdings required for its
  role.
- Reuse a station across roles where it meets each role's contract.
- Otherwise use a feature-specific fallback station.
- Retain record length, completeness, distance, method, and quality metadata.
- Preserve an existing approved role-aware mapping during a reference rebuild
  when the hospital coordinates are unchanged.
- Validate actual element availability for each ingested period; historical
  holding metadata alone does not guarantee a complete month.

### Regional thunder proxies

Six thunder mappings beyond the preferred 50 km distance are deliberately
approved as `regional_proxy` rather than rejected. They cover Gosford, Wyong,
Cowra, Forbes, Deniliquin, and Broken Hill. This limitation remains visible in
the mapping metadata and validation report.

### Current validated mapping

- 365 of 365 hospital/role mappings are approved.
- 99 unique BoM climate stations are selected.
- 359 mappings meet their preferred distance.
- 6 mappings are approved regional thunder proxies.

## 9. Weather feature definitions

### Decision

Raw ingestion retains daily/sub-daily observations. Monthly health-related
features are produced later in Silver, not embedded in raw acquisition.

| Feature | Decision |
| --- | --- |
| Temperature | Monthly minimum, mean, and maximum derived from daily minimum/maximum inputs |
| Extreme heat | Count days where daily maximum temperature is at least 35°C |
| Cold days | Count days where daily maximum temperature is at most 10°C |
| Cold nights | Count nights where daily minimum temperature is at most 5°C |
| Heatwaves | Use BoM Excess Heat Factor, including local climatology and the preceding 30 days |
| Rainfall | Daily minimum/mean/maximum, monthly total, and measurable-rain day count |
| Relative humidity | Minimum/mean/maximum across available 09:00 and 15:00 observations |
| Wind | Summarise routine wind speed separately from maximum wind gust |
| Thunderstorms | Count days on which thunder is recorded |

- Missing observations remain missing; they are not converted to zero.
- Temperature climatology for Excess Heat Factor is a separate versioned input
  and must not force current observations onto a more distant climate station.

## 10. BoM acquisition channel

### Decision

Do not automate scraping of public Climate Data Online pages. BoM blocks such
requests and directs automated consumers to public data feeds or Registered
User services. No Registered User agreement is available for this project.

Use the technically feasible sanctioned public products:

- `IDN60920.xml` as the current NSW station catalogue and BoM-ID-to-WMO-ID
  resolver;
- `IDN60801.{wmo_id}.json` as the rolling 72-hour station observation product.

### Daily observation and aggregation decision

Continue polling the rolling public observation feed every day and retain its
complete source payload in Bronze. The endpoint bundles temperature, rainfall,
relative humidity, wind, gust, and descriptive weather fields, so individual
elements are not selected at download time.

Do not switch routine ingestion to the completed monthly DWO product. Silver
processing will first deduplicate overlapping observations by station and
`aifstime_utc`, then calculate daily measures and monthly aggregates. This keeps
one consistent observation source for the available weather elements and makes
aggregation logic explicit and testable outside the raw ingestion layer.

The sampled sub-daily feed does not provide official daily extrema, and its
descriptive weather field is not yet a validated daily thunder indicator. Those
limitations remain documented problems rather than being hidden by the
aggregation step.

The collector:

- first validates all 365 approved mapping rows;
- fetches only unique mapped stations advertised by the current public feed;
- uses four parallel downloads and two Airflow retries;
- validates product identifier, WMO identity, and observation timestamps;
- stores the source XML and JSON without transformation;
- reports unsupported station IDs rather than silently choosing another site;
- relies on downstream deduplication by station and `aifstime_utc` because
  consecutive 72-hour files intentionally overlap.

### Raw public-feed paths

```text
data/raw/bom/nsw_observation_catalog/year=YYYY/month=MM/*.xml
data/raw/bom/station_observations/year=YYYY/month=MM/
  station_id=NNNNNN/*.json
```

### Current public-feed coverage

- 76 of the 99 mapped climate stations are present in the public real-time
  feed.
- All temperature, relative-humidity, and wind role mappings are supported.
- 49 of 73 rainfall role mappings are supported.
- 72 of 73 thunder role mappings are supported.
- The unsupported set comprises 22 rainfall-only stations affecting 24
  hospital rainfall mappings, plus one thunder station.
- No fallback to another already-downloaded station has been approved.

### Accepted limitations

- Collection cannot backfill observations from before the collector began.
- The free product is a rolling real-time feed, not the quality-controlled CDO
  historical archive.
- BoM warns that routine quality checks may not yet have been performed on
  these observations.
- A daily run is required even though the eventual analytical partition is
  monthly.
- Public-feed access is subject to BoM's copyright, disclaimer, and
  non-commercial-use conditions.

## 11. Reference artifacts and validation

### Decision

Generate and validate these reference artifacts together:

- `nsw_lhds.csv`
- `nsw_lhd_boundaries.geojson`
- `nsw_hospitals.csv`
- `hospital_weather_mapping.csv`
- `reference_validation_report.json`

The build fails on duplicate identities, broken relationships, missing required
coordinates, incomplete mapping coverage, or unapproved mapping rows. Warnings
are retained for intentional scope limitations such as the BHI-only hospital
population and regional thunder proxies.

The current reference report status is `passed_with_warnings`.

## 12. Environment and deployment choices

### Decision

- Python source is packaged from `src/` and installed with `pip install -e .`.
- `curl-cffi` is used for live HTTP retrieval where needed.
- `openpyxl` is used to read the BHI workbook.
- `tzdata` is an explicit dependency so `Australia/Sydney` calculations work
  correctly on Windows and Linux.
- Airflow 3 and its standard provider should be installed using Airflow's
  official constraints.
- Run Airflow under Linux/WSL2 or containers rather than native Windows.
- Data/reference roots and BoM download concurrency can be overridden with
  environment variables; credentials must never be committed.

## 13. Testing decisions and current evidence

### Decision

All source parsing, time boundaries, path construction, mapping gates,
deduplication, and reference relationships require offline tests. Live tests
are used only to verify the external source contract and write either to a
temporary directory or an explicitly requested raw location.

Current evidence:

- 29 unit tests pass.
- All project Python files parse successfully.
- The live BoM smoke test resolved and downloaded all 76 supported stations.
- The initial raw landing contains 76 JSON files and 9,499 validated
  observation rows, plus one NSW station-catalogue XML snapshot.

## 14. AIHW performance-data scope

### Decision

Exclude AIHW ED performance datasets from ingestion and forecasting. Remove the
AIHW ingestion namespace, Airflow placeholder, exploratory notebook, and raw
folder from the project structure.

Retain the AIHW MyHospitals spatial service only as supporting reference data
for resolving BHI hospital coordinates and names in
`map_hospitals_to_weather.py`. MyHospitals performance measures are not
ingested and do not become forecasting features.

### Rationale and consequence

The inspected AIHW ED measures are annual, released after their reporting
period, and substantially overlap the more timely NSW-specific BHI workbook.
Their additional admission-status breakdowns do not justify another production
pipeline for the current forecasting scope. Keeping the spatial lookup avoids
breaking the validated hospital-to-weather mapping while clearly separating
reference enrichment from analytical ingestion.

## 15. ABS SEIFA source contract

### Decision

- Ingest only the Statistical Area Level 1 (SA1) index workbook for each Census
  release: `Statistical Area Level 1, Indexes, SEIFA YYYY.xlsx`.
- Preserve the complete source workbook in Bronze, including all four indexes
  (IRSD, IRSAD, IER, and IEO), excluded areas, and geographic reference tables.
- Poll the official ABS latest-release page daily at 09:00 UTC and read its
  reference-period field rather than depending on email notifications.
- Require the page to retain the text `Latest release`. A missing or changed
  marker is treated as a source-contract failure requiring investigation.
- When the reference period has no existing snapshot, resolve and ingest the
  linked official SA1 workbook. Otherwise, avoid the large workbook download.
- Require HTTPS on `abs.gov.au`, an exact SA1 filename, the advertised Census
  year, Tables 1–7, expected headers, and 11-digit SA1 records before publishing
  the raw snapshot.
- Store immutable, hash-deduplicated snapshots under:

```text
data/raw/abs/seifa/census_year=YYYY/
  seifa_YYYY_YYYYMMDDTHHMMSSZ.xlsx
```

- Do not assign SEIFA based on a hospital's physical SA1. Patient residence or
  a defined patient-catchment correspondence is required before SEIFA becomes a
  forecasting feature.

### Rationale and consequence

SA1 is the foundation of the published SEIFA indexes and preserves the finest
available area resolution for later defensible aggregation. Keeping all four
indexes in the raw source does not commit the analytical model to using all
four. The public release page is a simpler source contract than mailbox access
and requires no stored email credentials. Period-only polling deliberately
does not detect a silent same-period workbook correction; the direct ingestion
function still hash-deduplicates revisions when one is explicitly downloaded.

## 16. Deferred decisions and work

The following are intentionally not treated as complete:

1. Define the Silver transformation that deduplicates overlapping station
   observations and calculates complete daily/monthly weather measures.
2. Decide how to resolve the one missing public-feed thunder mapping and
   validate whether the descriptive `weather` field supports a defensible daily
   thunder measure.
3. Select and version the long-term temperature climatology required for Excess
   Heat Factor.
4. Define minimum monthly completeness rules for each weather feature before a
   Silver aggregate can be considered usable.
5. Define the patient/catchment-to-SA1 transformation and decide whether a
   lower-frequency workbook hash check is needed for same-period corrections.
6. Add an authoritative statewide hospital register only if the analytical
   scope expands beyond the current 73 BHI ED hospitals.

