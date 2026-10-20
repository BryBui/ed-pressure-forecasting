Run from the repository root:

```bash
.venv-wsl/bin/python src/xgboost/LHDs/train_xgboost_lhd.py
```

The TOML specifies targets, shared XGBoost parameters and per-target selected
hyperparameters. To repeat the candidate comparison:

```bash
.venv-wsl/bin/python src/xgboost/LHDs/train_xgboost_lhd.py --tune
```

`--config`, `--panels`, `--output-dir` and `--model-dir` override the defaults.
The TOML `data.models` setting controls the model directory separately from
`data.output`, which controls forecasts and reports. Tuning records
winners in `training_report.json`; it does not rewrite the configuration.
When changing the data, review the new winners and update the TOML's `selected`
tables before subsequent runs.

All panel CSVs are pooled, with one-hot LHD identifiers. Each of the ten overall
BHI numerical measures and the panel pressure_index has its own regression model so missing labels for one
measure do not discard other measures. Labels are joined by LHD and exact next
calendar quarter. Every numeric panel feature (including existing lag columns)
is available at the end of the origin quarter; year/quarter become an origin
trend and forecast season. Missing covariates remain NaN for XGBoost to handle.
No future-quarter weather or outcomes enter the predictors. Static 2021 SEIFA
is retrospective: these results are not a strict historical as-of backtest.

For each measure, three expanding windows validate the last twelve labelled
quarters in four-quarter blocks. All LHDs for a quarter stay together. The lowest
mean absolute error selects among four regularised shallow-tree configurations;
this is a finite search, not a claim of globally optimal hyperparameters. The
validation MAE is a model-selection score, not an independent holdout estimate.
Persistence MAE uses the same validation origins where an origin value exists.
Final models refit on every available labelled pair from every panel. Rows
without a next-quarter label cannot be used as supervised training examples.

On the initial supplied panels, the selected XGBoost models had higher validation
MAE than persistence for all ten measures. The saved models implement the
requested XGBoost forecasts, but this run does not establish an improvement over
that baseline. See the per-target candidate scores in `tuning_report.json`.

Outputs in `data/gold/xgboost/LHDs`:

Models are saved separately under `models/xgboost/LHDs`, one native `.ubj`
file per target. The gold directory contains:

- `training_hyperparameters.json`: effective parameters for each target, including
  shared model settings, selected hyperparameters, boosting rounds and validation settings.
- `training_report.json`: ordered feature schema, configuration, XGBoost version,
  training counts, candidate MAEs and selected settings. For reloaded models,
  supply numeric matrices in exactly this feature order.
- `tuning_report.json`: the most recent full candidate comparison, retained
  when a later default run replaces the training report.
- `next_quarter_predictions.csv`: forecasts anchored to each LHD's last
  recorded BHI quarter, skipping trailing weather-only rows. The year and quarter
  identify the forecast quarter. Measures no longer reported use missing current
  inputs; their models retain the older labelled training history. Forecasts are
  unconstrained regression values;
  no clipping or rounding is applied.

The implementation uses the native training and persistence APIs documented in
[XGBoost's Python API](https://xgboost.readthedocs.io/en/stable/python/python_api.html).
The folder `src/xgboost` is a script directory, not a Python package: do not add
an `__init__.py` that would shadow the installed XGBoost library.

The configured pressure_index target is forecast directly by its own model.
It uses the existing panel index as its next-quarter label, with current-quarter
observations as inputs. Selected settings are in `[selected.pressure_index]`.
