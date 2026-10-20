# Healthcare Data System — Problem Register

This register records confirmed defects, data limitations, operational risks,
and incomplete work identified as of 19 October 2026. It complements
`decisions.md`: that file records what was chosen, while this file records what
is missing, unreliable, constrained, or still needs resolution.

Severity meanings:

- **Critical**: prevents the complete system from producing trustworthy outputs.
- **High**: materially limits required data or analytical correctness.
- **Medium**: creates a reliability, maintenance, or governance risk.
- **Low**: technical debt or a limitation with a contained current impact.

## Summary

| ID | Severity | Status | Problem |
| --- | --- | --- | --- |
| P-01 | High | Open | Automated quality-controlled Climate Data Online access is unavailable |
| P-02 | High | Open | Public BoM feed does not cover all approved weather stations |
| P-03 | High | Open | BoM observations cannot be backfilled before collection began |
| P-04 | High | Open | Public BoM observations are not routinely quality controlled |
| P-05 | High | Open | Official daily minimum and maximum temperatures are absent |
| P-06 | High | Open | Rainfall values require careful reconstruction and have mapping gaps |
| P-07 | High | Open | Thunderstorm identification is incomplete and ambiguous |
| P-08 | High | Open | Heatwave climatology and EHF calculation are not implemented |
| P-09 | Critical | Open | Silver and Gold transformations are not implemented |
| P-11 | Medium | Open | ABS SEIFA analytical mapping and same-period revision detection are incomplete |
| P-12 | High | Open | Monthly completeness and publication rules are not defined |
| P-13 | Medium | Open | A BoM outage longer than the rolling window causes permanent gaps |
| P-14 | Medium | Open | A failed BoM batch can leave a partial raw landing |
| P-15 | Medium | Open | BoM runs do not write a durable ingestion manifest |
| P-16 | Medium | Open | External source schemas and endpoints can change without notice |
| P-17 | Medium | Open | Hospital aliases and one special-case coordinate require maintenance |
| P-18 | Low | Accepted | Hospital reference is not a complete NSW hospital register |
| P-19 | High | Open | BoM public-feed licensing constrains future use and publication |
| P-20 | Medium | Open | Airflow has not been integration-tested in its deployment runtime |
| P-21 | Medium | Open | Production monitoring and alerting are absent |
| P-22 | Medium | Open | Daily per-station raw files create a small-file scaling problem |
| P-23 | Low | Open | Dependency resolution is not fully reproducible |
| P-24 | Medium | Open | The workspace is not currently a Git repository |
| P-25 | Low | Open | “Working day” holiday semantics are not authoritative |
| P-26 | Medium | Open | BHI discovery depends on mutable page markup and filenames |
| P-27 | Medium | Open | Station mappings can become stale between BHI releases |
| P-28 | Medium | Open | Missing values and source revisions have no analytical policy yet |
| P-29 | Low | Accepted | Monthly BHI polling can delay release detection |

## P-01 — Automated quality-controlled Climate Data Online access is unavailable

**Severity:** High  
**Status:** Open; constrained by external access

### Evidence

BoM blocked automated requests to the public Climate Data Online endpoint and
directed automated consumers to anonymous public feeds or a Registered User
service. This project has no Registered User agreement.

### Impact

The pipeline cannot automatically obtain the complete quality-controlled,
station-specific historical observations originally intended by the mapping.
The current public-feed implementation is a technically feasible substitute,
not an equivalent CDO archive.

### Current mitigation

Use the sanctioned NSW public observation catalogue and rolling 72-hour station
JSON products. Do not scrape public CDO pages.

### Resolution options

- Obtain a BoM Registered User agreement and implement its documented feed; or
- retain the public-feed limitations and redesign the feature/mapping contract
  around the data it actually supplies.

## P-02 — Public BoM feed does not cover all approved weather stations

**Severity:** High  
**Status:** Open

### Evidence

- 76 of 99 unique approved mapped climate stations appear in the public feed.
- Temperature mappings supported: 73 of 73.
- Relative-humidity mappings supported: 73 of 73.
- Wind mappings supported: 73 of 73.
- Rainfall mappings supported: 49 of 73.
- Thunder mappings supported: 72 of 73.
- The unsupported set contains 22 rainfall-only stations affecting 24 hospital
  mappings, plus one thunder station.

### Impact

Monthly datasets cannot currently provide the approved rainfall source for 24
hospitals or the approved thunder source for one hospital.

### Current mitigation

Unsupported station IDs are returned explicitly by the Airflow task. The code
does not silently substitute another station.

### Required decision

Review and explicitly approve feed-aware fallback stations, obtain another
sanctioned source, or accept missing weather features for those hospitals.

## P-03 — BoM observations cannot be backfilled before collection began

**Severity:** High  
**Status:** Open

### Evidence

The free station JSON product contains only a rolling 72-hour history. Initial
collection began in October 2026.

### Impact

- Historical weather cannot currently be aligned with BHI records back to 2010.
- The model cannot be trained across the BHI history from this source alone.
- Rebuilding a deleted raw layer would not reproduce earlier observations.

### Resolution options

Obtain historical data through a registered/requested BoM service or select a
separate legally usable historical dataset with an explicit provenance and
comparability assessment.

## P-04 — Public BoM observations are not routinely quality controlled

**Severity:** High  
**Status:** Open; accepted for raw acquisition only

### Evidence

BoM describes the Latest Weather Observation feed as real-time data on which
routine quality checks are not normally performed.

### Impact

Erroneous spikes, delayed reports, instrument faults, and later corrections can
affect derived ED-pressure features.

### Current mitigation

Raw snapshots are immutable and revisions can be retained. No quality flags or
Silver-layer anomaly rules have yet been implemented.

### Required work

Define validation ranges, temporal consistency rules, revision handling, and a
quality status for every Silver aggregate.

## P-05 — Official daily minimum and maximum temperatures are absent

**Severity:** High  
**Status:** Open

### Evidence

Across the initial 76 station files and 9,499 observation rows:

- `air_temp` is present, but
- no row contains an official `maximum_air_temperature` field, and
- no row contains an official `minimum_air_temperature` field.

### Impact

Extreme-heat days, cold days, cold nights, daily mean temperature, and EHF
cannot exactly reproduce official daily climate observations. Minima and maxima
derived from half-hour/special observations may miss an inter-observation
extreme.

### Current mitigation

All 73 temperature mappings have sub-daily `air_temp` coverage in the initial
landing. This allows an approximation but is not yet approved as analytically
equivalent to official daily extrema.

### Required work

The selected direction is to derive extrema from the daily-polled sub-daily
observations in Silver. Document and test sampling bias, minimum observation
coverage, and the conditions under which a derived daily value is rejected.

## P-06 — Rainfall values require careful reconstruction and have mapping gaps

**Severity:** High  
**Status:** Open

### Evidence

The public JSON exposes `rain_trace`, a cumulative rainfall-since-09:00 value,
not a clean interval or final daily total. Only 49 of 73 approved rainfall
mappings are present in the feed.

### Impact

Naively summing observations would severely over-count rainfall. Missing the
09:00 reset/final observation can also understate or misassign daily totals.

### Required work

- Reconstruct each BoM rainfall day using local station time and reset logic.
- Detect accumulated/missing observations and source revisions.
- Define whether a later observation may substitute for a missing 09:00 value.
- Resolve the 24 unsupported hospital rainfall mappings.

Daily public-feed ingestion remains the selected source. Rainfall reconstruction
and completeness checks therefore belong in the Silver transformation and must
be tested before the monthly aggregate is treated as usable.

## P-07 — Thunderstorm identification is incomplete and ambiguous

**Severity:** High  
**Status:** Open

### Evidence

- One approved thunder station is absent from the public feed.
- Only 62 of the 72 feed-supported hospital thunder mappings emitted any
  non-placeholder `weather` description in the initial 72-hour window.
- A blank or `-` weather field cannot safely be interpreted as “no thunder”.
- Station selection used total-cloud holdings as a proxy for visual-weather
  capability; this does not guarantee complete thunder reporting.
- Six mappings are deliberately distant regional proxies.

### Impact

Thunderstorm-day counts may contain false negatives and have inconsistent
spatial representativeness.

### Required work

Define recognised thunder weather codes, distinguish “not observed” from “not
reported”, establish a minimum reporting-completeness rule, and evaluate a
dedicated lightning dataset if thunder is important to the model.

## P-08 — Heatwave climatology and EHF calculation are not implemented

**Severity:** High  
**Status:** Open

### Evidence

The feature contract requires BoM Excess Heat Factor using local climatology
and the preceding 30 days, but no climatology dataset or transformation exists.

### Impact

Heatwave event counts, day counts, and duration statistics cannot be produced.
The rolling public feed also cannot provide the required historical baseline.

### Required work

Select, license, version, and station-align a long-term daily temperature
climatology; then implement and validate EHF across month boundaries.

## P-09 — Silver and Gold transformations are not implemented

**Severity:** Critical  
**Status:** Open

### Evidence

`data/silver/` and `data/gold/` contain only placeholder files. No production
code currently:

- deduplicates overlapping 72-hour snapshots;
- converts observations into hospital-level daily records;
- calculates monthly weather features;
- standardises BHI or SEIFA data;
- joins weather and healthcare data; or
- creates forecasting-ready outputs.

### Impact

The repository currently performs ingestion and reference preparation, but it
does not yet produce the analytical dataset required for ED-pressure modelling.

## P-11 — ABS SEIFA analytical mapping and revision detection are incomplete

**Severity:** Medium  
**Status:** Open

### Evidence

`src/ingestion/seifa.py` discovers, validates, and immutably stores official SA1
index workbooks. `airflow/dags/ingest_seifa.py` polls the public ABS
latest-release page daily, validates its release marker and reference period,
and downloads the workbook only when the period is not already stored. This
does not detect a silent correction to the workbook while the reference period
remains unchanged. The analytical link from hospital activity to patient-area
SEIFA also remains undefined.

### Remaining work

- Decide whether to periodically download and hash the current-period workbook
  to detect corrections that do not change the reference period.
- Define the patient-residence or catchment correspondence used to aggregate
  SA1 values; do not use the hospital building's SA1 as a patient proxy.

## P-12 — Monthly completeness and publication rules are not defined

**Severity:** High  
**Status:** Open

### Evidence

The release-window helper determines when a completed-month check may begin,
but no rule defines when each station/feature month is sufficiently complete to
publish to Silver.

### Impact

Monthly features could be produced from an incomplete set of days or reporting
times without an explicit failure.

### Required work

Define per-feature requirements such as expected days, minimum valid-day
percentage, required 09:00/15:00 observations, treatment of traces, and whether
late source revisions reopen a previously published month.

## P-13 — A BoM outage longer than the rolling window causes permanent gaps

**Severity:** Medium  
**Status:** Open

### Evidence

The DAG runs once daily and the source retains approximately 72 hours. Daily
files overlap, which protects against a short failure, but not an outage or
scheduler stoppage extending beyond the source window.

### Impact

Lost observations cannot be recovered without another data source.

### Current mitigation

Airflow retries twice and overlapping snapshots provide roughly two days of
additional recovery margin.

### Required work

Alert before the recoverable window expires and consider running more than once
daily if the operational environment is unreliable.

## P-14 — A failed BoM batch can leave a partial raw landing

**Severity:** Medium  
**Status:** Open

### Evidence

Station downloads are individually atomic, but the complete 76-station batch is
not transactional. If one request fails after others succeed, the task fails
while already-created station files remain. A retry safely reuses them, but a
permanent failure leaves an incomplete run without a durable completion marker.

### Impact

Downstream code could mistake a partial batch for a complete collection cycle.

### Required work

Write a run manifest only after all expected stations complete, or store an
explicit failed/partial manifest that downstream processing must reject.

## P-15 — BoM runs do not write a durable ingestion manifest

**Severity:** Medium  
**Status:** Open

### Evidence

The task returns counts and unsupported station IDs through Airflow, but does
not persist a manifest containing every source URL, local path, checksum,
source timestamp, row count, status, and error.

### Impact

Audit and reconciliation require re-reading directories and recomputing file
hashes. Airflow XCom retention may be shorter than raw-data retention.

### Required work

Add a versioned manifest per successful or partial collection run.

## P-16 — External source schemas and endpoints can change without notice

**Severity:** Medium  
**Status:** Open

### Evidence

The live test already found two valid BoM JSON shapes: `header` may be an object
or a one-item array. BHI, NSW spatial services, BoM station lists, and public
observation endpoints are all external contracts outside this repository.

### Impact

Markup, filenames, field names, identifiers, redirects, anti-automation rules,
or service availability can break ingestion/reference builds.

### Current mitigation

Strict parsers fail loudly, source payloads are retained, and known header
variants are tested.

### Required work

Add scheduled contract tests, actionable alerts, and documented recovery
procedures without relaxing identity/schema validation.

## P-17 — Hospital aliases and one special-case coordinate require maintenance

**Severity:** Medium  
**Status:** Open maintenance risk

### Evidence

Eighteen BHI/MyHospitals relationships use a reviewed hard-coded alias table,
and Tweed Valley Hospital is resolved through a separate spatial-service ID.

### Impact

Hospital renames, closures, replacements, and identifier changes can invalidate
coordinate matching or preserve a stale location.

### Current mitigation

All 73 hospitals currently resolve and validation fails if a location is
missing.

### Required work

Review aliases on every BHI release and record alias provenance/effective dates
in a data file rather than only in source code.

## P-18 — Hospital reference is not a complete NSW hospital register

**Severity:** Low  
**Status:** Accepted for current scope

### Evidence

The reference contains only 73 hospitals present in the latest BHI ED quarter;
an authoritative statewide hospital register was not checked.

### Impact

The reference must not be reused for claims about all NSW hospitals or services.

### Current mitigation

Scope is explicit in the validation report and documentation. This becomes an
open defect only if the project expands beyond BHI ED.

## P-19 — BoM public-feed licensing constrains future use and publication

**Severity:** High  
**Status:** Open governance requirement

### Evidence

BoM's anonymous/public products are subject to its copyright and disclaimer,
including non-commercial and redistribution constraints. A future Azure
deployment or published derivative may change the use context.

### Impact

Technical availability does not by itself authorise commercial use,
redistribution, or every form of publication.

### Required work

Obtain a documented licensing review before external publication, commercial
use, data sharing, or production migration.

## P-20 — Airflow has not been integration-tested in its deployment runtime

**Severity:** Medium  
**Status:** Open

### Evidence

Unit tests and Python syntax checks pass, but Airflow is intentionally not a
project dependency and no running scheduler/database is configured in this
workspace. DAG import, task execution, cross-DAG triggering, retries, XCom, and
filesystem permissions have not been tested under the intended WSL2/container
runtime.

### Required work

Install Airflow with official constraints in Linux, run `airflow dags
list-import-errors`, execute DAG tests, and verify scheduler behavior against
mounted data paths.

## P-21 — Production monitoring and alerting are absent

**Severity:** Medium  
**Status:** Open

### Evidence

There are no configured notifications, data-freshness checks, coverage
dashboards, service-level objectives, or alerts for approaching the 72-hour
recovery limit.

### Impact

A failed or silently stale ingestion may not be noticed before data becomes
unrecoverable.

### Required work

Alert on task failure, stale latest observation time, falling station coverage,
partial manifests, unexpected source revision, and missing completed-month
outputs.

## P-22 — Daily per-station raw files create a small-file scaling problem

**Severity:** Medium  
**Status:** Open optimisation

### Evidence

One successful daily run creates approximately 76 station JSON files plus a
catalogue file. This is roughly 2,300 station files per month before revisions.

### Impact

Large numbers of small files increase object-store listing, metadata, and
Silver-processing overhead after an Azure migration.

### Current rationale

Per-station files retain exact source responses and make retry deduplication
simple.

### Resolution options

Keep raw files immutable but compact parsed, deduplicated observations into
partitioned Parquet in Silver. Do not compact by rewriting Bronze history.

## P-23 — Dependency resolution is not fully reproducible

**Severity:** Low  
**Status:** Open

### Evidence

`pyproject.toml` declares minimum versions but there is no application lockfile
or fully pinned deployment constraints file. Airflow itself must be installed
separately.

### Impact

Future installs may resolve different transitive versions and behave
differently from the tested environment.

### Required work

Define a deployment-specific lock/constraints process while retaining Airflow's
official constraint requirements.

## P-24 — The workspace is not currently a Git repository

**Severity:** Medium  
**Status:** Open environment problem

### Evidence

No `.git` directory is present in the workspace.

### Impact

There is no local change history, branching, review trail, or reliable rollback
for code and reference-data changes.

### Required work

Initialise or reconnect the intended repository before broader development.
Continue excluding downloaded raw data as configured in `.gitignore`.

## P-25 — “Working day” holiday semantics are not authoritative

**Severity:** Low  
**Status:** Open

### Evidence

The release helper treats Monday through Friday as working days. It does not use
an official BoM operating calendar or public-holiday calendar.

### Impact

An earliest-check boundary may be one day early around a holiday. Source-content
validation still prevents a calendar date alone from publishing a month, so the
impact is additional harmless polling rather than false data creation.

### Resolution option

Keep the current source-aware behavior or configure an authoritative BoM
publication calendar if one becomes available.

## P-26 — BHI discovery depends on mutable page markup and filenames

**Severity:** Medium  
**Status:** Open maintenance risk

### Evidence

BHI discovery relies on a known link class/wrapper pattern and a workbook
filename matching `BHI_DATA_HQ_ED_Jan2010{quarter-end}{year}.xlsx`.

### Impact

A website redesign or filename change can stop release discovery even when a
valid new workbook exists.

### Current mitigation

There is an explicit link-class path, a legacy wrapper fallback, strict workbook
validation, retries, and unit coverage for current markup.

### Required work

Alert clearly on discovery failure and update the parser only after retaining a
fixture for the new official page shape.

## P-27 — Station mappings can become stale between BHI releases

**Severity:** Medium  
**Status:** Open

### Evidence

Reference rebuilding is triggered by a new BHI workbook. BoM station closures,
element changes, feed removal, or hospital coordinate corrections can occur
without a BHI release. Approved mappings are intentionally preserved when
hospital coordinates are unchanged.

### Impact

A previously valid mapping may remain approved while current observations stop
or a more appropriate station becomes available.

### Required work

Add an independent periodic station/mapping audit and require review when
coverage, distance, station status, or coordinates change.

## P-28 — Missing values and source revisions have no analytical policy yet

**Severity:** Medium  
**Status:** Open

### Evidence

The raw layer correctly retains missing values and revised snapshots, but no
Silver rule determines:

- which revision wins;
- whether previously published aggregates are recomputed;
- how missing days/times affect monthly statistics;
- whether limited interpolation is permitted; or
- how imputed and observed values are distinguished.

### Impact

Different analysts could derive inconsistent results from the same raw files.

### Required work

Define deterministic revision precedence, completeness, recomputation,
imputation, and lineage rules before producing analytical outputs.

## P-29 — Monthly BHI polling can delay release detection

**Severity:** Low  
**Status:** Accepted

### Evidence

BHI publishes the ED dataset quarterly, while the pipeline checks at 09:00 UTC
only on the first day of each month. BHI does not provide a server-side content
hash, ETag, or Last-Modified validator, so every reliable comparison requires
downloading the complete workbook.

### Impact

A workbook released just after a monthly check may not be discovered until the
first day of the following month. An in-place revision has the same potential
detection delay.

### Rationale

This delay is accepted because monthly polling substantially reduces repeated
downloads of an approximately 18.5 MB quarterly workbook. SHA-256 comparison
still prevents duplicate storage and unnecessary reference rebuilding.

## Accepted conditions that should not be mistaken for new defects

- The 73-hospital cohort is intentionally scoped to current BHI ED reporting.
- Seven historical-only BHI hospitals are intentionally excluded from the
  active reference but remain recoverable from raw data.
- Three specialty-network hospitals intentionally have no geographic LHD ID.
- Six distant thunder mappings are explicitly approved regional proxies.
- Source files are intentionally immutable, so revisions increase file count.

