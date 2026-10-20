# Project instructions for AI agents

## Working style

- Work in small, reviewable diffs.
- Explain what is happening, what changed, why it changed, and how it connects
  to the existing code.
- Identify the relevant tests for every change and run them before handing off.
- Preserve unrelated user changes and existing raw/reference data.

## Runtime environment

- This project is developed and tested in Linux under WSL because Apache
  Airflow is a Linux workload.
- Use the project-local `.venv-wsl` interpreter for all Python, tests, scripts,
  and Airflow commands. Do not use the Windows `.venv`.
- When already inside WSL, run `.venv-wsl/bin/python ...`. From PowerShell, run
  `wsl.exe .venv-wsl/bin/python ...`.
- Keep the project in WSL's native Linux filesystem under `/home/...`, not
  `/mnt/c/...`, for faster package installation and Airflow file operations.
- Do not copy an old virtual environment when relocating the project. Recreate
  it in the new Linux folder; `lib64` is a generated virtual-environment
  symlink and does not need to be copied.

Environment bootstrap:

```bash
python3 -m venv .venv-wsl
.venv-wsl/bin/python -m pip install --upgrade pip
.venv-wsl/bin/python -m pip install -e .
.venv-wsl/bin/python -m pip install "apache-airflow==3.3.2"
```

At the last verified handoff, the environment used Python 3.14.4, Apache
Airflow 3.3.2, and GeoPandas 1.2.0.

## Current SA1-to-LHD work

- `src/reference/SA1_to_LHD.py` maps 2021 NSW Statistical Area Level 1
  polygons to the existing NSW Local Health District polygons.
- GeoPandas is a declared dependency in `pyproject.toml`.
- The script reads SA1 and LHD GeoJSON, filters national SA1 input to NSW when
  an ABS state-code field exists, validates identifiers and geometry, projects
  both layers to Australian Albers (`EPSG:3577`), and performs a polygon
  intersection.
- If an SA1 crosses an LHD boundary, it is assigned to the LHD with the largest
  intersecting area. Output includes the overlap percentage and assignment
  method.
- The existing LHD input is
  `data/reference/nsw_lhd_boundaries.geojson`. It contains 15 valid official
  NSW Health LHD features in `EPSG:4326`.
- The SEIFA source uses 2021 SA1 codes, so use the official 2021 ABS SA1
  boundaries rather than 2026 boundaries.
- Official ABS boundary polygons exist for the 2021 SA1 geography, but the
  boundary file had not yet been downloaded or copied into this repository at
  the last handoff. The stored SEIFA workbook contains SA1 codes and index
  values, not polygon geometry. Consequently, the production
  `data/reference/sa1_to_lhd.csv` had not yet been generated.
- Do not assign patient socioeconomic status from a hospital's physical SA1.
  This correspondence supports defensible residence/catchment aggregation.

Run the mapper after the 2021 SA1 GeoJSON is available:

```bash
.venv-wsl/bin/python -m reference.SA1_to_LHD \
  --sa1 path/to/SA1_2021_NSW.geojson
```

The default LHD input is `data/reference/nsw_lhd_boundaries.geojson`; the
default output is `data/reference/sa1_to_lhd.csv`.

## Verification commands

Focused spatial tests:

```bash
.venv-wsl/bin/python -m unittest tests.test_sa1_to_lhd -v
```

Full offline suite:

```bash
.venv-wsl/bin/python -m unittest discover -s tests -v
```

At the last handoff, all 3 focused spatial tests and all 39 repository tests
passed under WSL. The focused tests cover largest-area assignment, NSW
filtering, unmatched-SA1 rejection, and real GeoJSON-to-CSV I/O.
