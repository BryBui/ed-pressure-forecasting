# LHD quarterly panels

Regenerate from the repository root:

```bash
.venv-wsl/bin/python -m transform.panels.LHD_panels
```

Outputs are `data/silver/LHD-panels/<bhi_lhd_id>.csv`, one chronologically sorted row per calendar
quarter, spanning BHI and mapped weather observations (including intervening
missing quarters). All BHI value columns are retained. Static socioeconomic
columns use the `socioeconomic__` prefix. `nsw_lhds.csv` bridges the weather
mapping's geographic LHD identifiers to BHI/SEIFA names and boundary codes.

Weather columns use `weather__<six-digit station id>__<source column>`.
Each unique approved station mapped to any hospital in the LHD is included once,
across all mapping observation groups. All numeric station features and coverage
measures are preserved; station names and first/last observation timestamps
remain in the source CSV rather than the feature matrix. Station features are
not averaged or weighted by hospital count. An observation-group mapping does
not guarantee every weather variable is available; source blanks are retained.
No thunderstorm feature is invented when absent from station_quarters.csv.
Panels have different station columns because each LHD has different stations.

`__lag_1` through `__lag_8` contain the dynamic feature from exactly that many
calendar quarters earlier, within the same LHD. Static socioeconomic values are
not lagged. The hyperparameters are stored in `src/transform/panels/lhd_panels.toml` under `[panel]` as
`lags = [1, 2, 3, 4, 5, 6, 7, 8]`. The script reads this file each run.
Use `--config path/to/config.toml` for another config or `--lags 1 2 4 8`
for a command-line override. Other input/output paths can
be overridden; run with `--help` for options. Blank cells mean unavailable,
suppressed, or not yet observed, never zero. Boolean weather flags become 0/1.

For forecasting quarter t, select lagged BHI/weather columns, year/quarter and
appropriate static covariates; current-quarter BHI (including pressure_index)
are potential targets and current-quarter weather is only usable when known at
the prediction cutoff. Actual publication delays may require longer lags.
Exclude lhd_id/lhd_name from numeric predictors. Split chronologically, remove
rows without the selected target, and fit any imputation/scaling/feature
selection on the training partition only. Do not backfill from future values.
No imputation or scaling is applied by this script. Models requiring complete
numeric matrices need a training-fitted missing-value strategy; preserve the
coverage flags when using partial weather observations.

SEIFA is a descriptive 2021 snapshot repeated across quarters, not historical
as-of data. Using it before its availability is a retrospective assumption.

At generation, BHI spans 2010 Q1–2026 Q2 and weather only 2026 Q4, which is
incomplete. The 15 outputs each contain 68 rows through 2026 Q4. There are
currently no quarters with both observed BHI and weather; historical weather
must be acquired before evaluating a model that uses observed weather lags.

# Hospital quarterly panels

```bash
.venv-wsl/bin/python -m transform.panels.hospital_panels
```

`hospital_panels.py` combines `data/silver/linking/hospital_bhi_lags.csv`,
`data/silver/linking/hospital_socioeconomic.csv`, and the same station-quarter
weather source using each hospital's approved weather mappings. Hospital names
in BHI are matched through `data/reference/nsw_hospitals.csv`; stable BHI IDs
are checked against the socioeconomic and weather mapping inputs. Specialty
network hospitals are included regardless of geographic LHD membership.

Outputs are `data/silver/hospital-panels/<bhi_hospital_id>.csv`.
`src/transform/panels/hospital_panels.toml` controls the default lags 1–8.
`--config`, `--lags`, and input/output overrides work as for LHD panels.
The hospital builder shares the LHD CSV, numeric, calendar and TOML readers.

Each hospital retains only its own deduplicated mapped stations, all BHI
features, static catchment SEIFA features and exact-quarter dynamic lags.
Hospital catchments are geographic proxies, not observed patient residence or
flows. Their SEIFA summaries do not describe individual patients. The same
missing-value, retrospective SEIFA and forecast-cutoff guidance above applies;
exclude hospital_id/hospital_name from numeric predictors.

At generation, 73 hospital panels contain 68 rows each (4,964 total), spanning
2010 Q1–2026 Q4. Weather is still only available for incomplete 2026 Q4.
