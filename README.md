# 🏥 NSW ED Pressure Forecasting

A data pipeline and dashboard for exploring emergency department pressure across
NSW hospitals and Local Health Districts (LHDs). It combines BHI performance data,
BoM weather observations and ABS socioeconomic indices to build quarterly panels
and next-quarter XGBoost forecasts.

## 🚀 Get started

Use **Linux or WSL2**, with the project stored under `/home/...`. Run commands
from the repository root using the project’s `.venv-wsl` environment.

To run the demo after installing the dependencies below and those in
`dashboard-dash-mantine/requirements.txt`:

```bash
.venv-wsl/bin/python demo_launcher.py
```

This starts Airflow at http://127.0.0.1:8080 and the dashboard at
http://127.0.0.1:8050, enables the ingestion/rebuild DAGs, and keeps both running
until **Ctrl+C**. Keep the terminal open and the computer awake. Logs, the
Airflow database, and generated login credentials are retained under `.demo/`;
the launcher prints the password-file location for user `admin`.
Existing schedules remain in effect (BHI monthly; BoM and SEIFA daily).
It does not force a download or model rebuild each time it starts. Existing
dashboard data is available while Airflow waits for its next scheduled check;
use the Airflow UI to trigger ingestion or a rebuild during a demonstration.
Refresh the dashboard after a completed publication to see the updated data.

If the demo launcher fails, launch just the dashboard from the repository root:

```bash
.venv-wsl/bin/python dashboard-dash-mantine/app.py
```

Open **http://127.0.0.1:8050**. This reads existing local pipeline outputs without
starting Airflow. Keep the terminal open; press **Ctrl+C** to stop the dashboard.
See [Run the dashboard](#-run-the-dashboard) below for dependency installation.

Use `--airflow-port` / `--dashboard-port` if the defaults are occupied, or
`--help` for other options. The demo uses Airflow's local
[standalone mode](https://airflow.apache.org/docs/apache-airflow/stable/start.html).

```bash
python3 -m venv .venv-wsl
.venv-wsl/bin/python -m pip install --upgrade pip
.venv-wsl/bin/python -m pip install -e .
```

For scheduled pipelines, also install Airflow:

```bash
.venv-wsl/bin/python -m pip install "apache-airflow==3.3.2"
```

Downloaded data, large boundary layers, generated datasets and trained models
are excluded from Git. A fresh clone needs these inputs before the full pipeline
or dashboard can run; smaller reference tables are included.

## 📊 Run the dashboard

```bash
.venv-wsl/bin/python -m pip install -r dashboard-dash-mantine/requirements.txt
.venv-wsl/bin/python dashboard-dash-mantine/app.py
```

Open **http://127.0.0.1:8050** to explore geographic mappings, socioeconomic
profiles, current ED trends and forecasts. See the
[dashboard guide](dashboard-dash-mantine/README.md) for required input files.

### Dashboard preview

**Data engineering**

![Data engineering dashboard](https://github.com/user-attachments/assets/8ac4cef3-d724-4277-bb3a-65a7b01a0baa)

**Forecasting**

![Forecasting dashboard](https://github.com/user-attachments/assets/53f8a5ea-813c-453d-ad1f-2cd4618004ba)

## How the data flows

```text
BHI performance + BoM weather + ABS SEIFA / SA1 boundaries
                         ↓
            Immutable raw snapshots
                         ↓
       Validated geography and reference mappings
                         ↓
          Quarterly hospital and LHD panels
                         ↓
          XGBoost forecasts → Dashboard
```

| Source | Collection | Purpose |
| --- | --- | --- |
| BHI | Monthly release check; new workbooks trigger reference refreshes | ED performance and hospital/LHD identities |
| BoM | Daily snapshots of rolling public observation feeds | Weather features for mapped hospital stations |
| ABS | Daily SEIFA release check, followed by matching SA1 boundaries | Socioeconomic and geographic reference data |

Raw snapshots are immutable and duplicate-safe. The
[pipeline guide](docs/pipeline-guide.md) covers schedules, source contracts,
station selection, reference outputs and boundary mapping.

## Run workflows manually

Preview ingestion paths without network requests:

```bash
.venv-wsl/bin/python -m ingestion.demo_ingest --explain-only
```

Collect available source data with `ingestion.demo_ingest` (omit
`--explain-only`), or select one source with `--source bhi`, `--source bom` or
`--source seifa`. Matching SA1 boundary downloads are a separate step.

Once raw data and reference mappings are available, build the transformed data:

```bash
.venv-wsl/bin/python -m transform.bhi.bhi_ed_to_csv
.venv-wsl/bin/python -m transform.bhi.hospital_bhi_ed_lags
.venv-wsl/bin/python -m transform.bhi.lhd_bhi_ed_lags
.venv-wsl/bin/python -m transform.socioeconomic.lhd_socioeconomic
.venv-wsl/bin/python -m transform.socioeconomic.hospital_socioeconomic
.venv-wsl/bin/python -m transform.bom.bom_to_quarters
.venv-wsl/bin/python -m transform.panels.LHD_panels
.venv-wsl/bin/python -m transform.panels.hospital_panels
```

Use `--help` for each script’s input and output options. BHI pressure weights
are configured in `src/transform/bhi/pressure_indices.toml`.

Train forecasts from the generated panels:

```bash
.venv-wsl/bin/python src/xgboost/LHDs/train_xgboost_lhd.py
.venv-wsl/bin/python src/xgboost/Hospitals/train_xgboost_hospital.py
```

See the [panel guide](src/transform/panels/README.md) and the
[LHD](src/xgboost/LHDs/README.md) / [hospital](src/xgboost/Hospitals/README.md)
model guides for configuration and evaluation details.

## Project layout

| Path | Contents |
| --- | --- |
| `airflow/dags/` | Scheduled ingestion and triggered forecast rebuilds |
| `src/pipeline/` | Pinned builds, validation and release publication |
| `src/ingestion/` | Source downloads and raw storage |
| `src/reference/` | Geographic validation and mappings |
| `src/transform/` | BHI, weather, socioeconomic and panel processing |
| `src/xgboost/` | Training scripts and model configuration |
| `dashboard-dash-mantine/` | Interactive Dash dashboard |
| `data/` | Raw snapshots, references, Silver tables and Gold outputs |
| `models/` | Generated trained models |
| `tests/` | Offline pipeline and model tests |

## Interpretation notes

- **Hospital scope:** the reference covers hospitals in BHI’s latest ED quarter,
  rather than every hospital in NSW.
- **Socioeconomic data:** SEIFA describes residence or defined catchment areas.
  A hospital building’s SA1 does not describe its patients.
- **Weather coverage:** public feeds are provisional, station coverage varies,
  and collection cannot backfill observations from before it started. The
  current panels have no overlapping observed BHI and weather quarters.
- **Forecast evidence:** initial LHD XGBoost validation did not beat persistence
  for the ten ED measures. Static 2021 SEIFA also makes historical evaluation
  retrospective.

## 🧪 Tests

```bash
.venv-wsl/bin/python -m unittest discover -s tests -v
.venv-wsl/bin/python -m unittest discover -s dashboard-dash-mantine -p 'test_*.py' -v
```

Both suites run offline. Airflow deployment checks are listed in the
[pipeline guide](docs/pipeline-guide.md#installation-and-tests).
