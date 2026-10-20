"""Stage reproducible builds and publish completed releases on a shared Linux disk.

Airflow calls these functions independently; no live outputs change until publish.
All workers must share the project directory and its project-local interpreter.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("data/silver", "data/gold", "models")


def checksum(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_run(root: Path, run_id: str, conf: dict) -> str:
    """Pin the exact BHI workbook, existing weather, SEIFA, references and code.

    Re-entering an Airflow run reuses its original snapshot, even if newer source
    files have arrived. A new manual run ID deliberately takes a fresh snapshot.
    """
    root = root.resolve()
    source = Path(conf["path"]).resolve()
    expected = conf["sha256"]
    raw = Path(os.environ.get("RAW_DATA_ROOT", root / "data/raw")).resolve()
    reference = Path(os.environ.get("REFERENCE_DATA_ROOT", root / "data/reference")).resolve()
    if not source.is_relative_to(raw / "bhi/ed_performance"):
        raise ValueError("BHI input must be an immutable snapshot under RAW_DATA_ROOT/bhi/ed_performance")
    from transform.bhi.bhi_ed_to_csv import SNAPSHOT_PATTERN
    match = SNAPSHOT_PATTERN.fullmatch(source.name)
    if not match or match[1] != conf["reporting_period"]:
        raise ValueError("BHI filename and reporting_period must agree")
    if checksum(source) != expected:
        raise ValueError("BHI checksum does not match the ingestion result")
    directory = root / "data/.pipeline/runs" / sha256(run_id.encode()).hexdigest()
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["bhi"] != {"path": str(source), "sha256": expected,
                               "reporting_period": conf["reporting_period"]}:
            raise ValueError("Cannot change inputs of an existing pipeline run")
        return str(directory)
    if not (reference / "nsw_sa1_boundaries.geojson").is_file():
        raise FileNotFoundError("Run the SA1 mapper first: missing reference/nsw_sa1_boundaries.geojson")
    directory.parent.mkdir(parents=True, exist_ok=True)
    # Incomplete preparation is never reused or exposed as a release.
    staging = directory.with_name(f".{directory.name}.{uuid4().hex}.tmp")
    staging.mkdir()
    try:
        shutil.copytree(root / "src", staging / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        shutil.copytree(reference, staging / "inputs/reference")
        shutil.copytree(reference, staging / "data/reference")
        snapshots = sorted((raw / "abs/seifa/census_year=2021").glob("seifa_2021_*.xlsx"))
        snapshots = [p for p in snapshots if not p.name.endswith(".part.xlsx")]
        if not snapshots:
            raise FileNotFoundError("No 2021 SEIFA snapshot available")
        files = [(source, Path("data/raw") / source.relative_to(raw)),
                 (snapshots[-1], Path("inputs/seifa.xlsx"))]
        weather = sorted((raw / "bom/station_observations").rglob("*.json"))
        if not weather:
            raise FileNotFoundError("No BoM station observation snapshots available")
        files.extend((p, Path("data/raw") / p.relative_to(raw)) for p in weather)
        for original, relative in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        if checksum(staging / "data/raw" / source.relative_to(raw)) != expected:
            raise ValueError("BHI changed while preparing the run")
        for name in OUTPUTS:
            (staging / name).mkdir(parents=True, exist_ok=True)
        inventory = {str(p.relative_to(staging)): checksum(p)
                     for base in ("src", "inputs", "data/raw")
                     for p in sorted((staging / base).rglob("*")) if p.is_file()}
        write_json(staging / "manifest.json", {
            "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
            "bhi": {"path": str(source), "sha256": expected,
                    "reporting_period": conf["reporting_period"]},
            "inputs": inventory,
        })
        staging.rename(directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return str(directory)


def commands(stage: str) -> list[list[str]]:
    """Use existing CLI defaults inside the isolated project-shaped workspace."""
    def module(name, *args):
        return ["-m", name, *args]
    if stage == "references":
        return [["-c", "from pathlib import Path; from reference.validate_references import build_reference_data; "
                 "build_reference_data(Path('data/raw'), Path('data/reference'))"],
                module("reference.SA1_to_LHD", "--sa1", "inputs/reference/nsw_sa1_boundaries.geojson")]
    if stage == "silver":
        return [module("transform.bhi.bhi_ed_to_csv"),
                module("transform.bhi.hospital_bhi_ed_lags"),
                module("transform.bhi.lhd_bhi_ed_lags"),
                module("transform.bom.bom_to_quarters"),
                module("transform.socioeconomic.lhd_socioeconomic", "--seifa", "inputs/seifa.xlsx"),
                module("transform.socioeconomic.hospital_socioeconomic", "--seifa", "inputs/seifa.xlsx"),
                module("transform.panels.hospital_panels"), module("transform.panels.LHD_panels")]
    scripts = {"train_hospitals": "Hospitals/train_xgboost_hospital.py",
               "train_lhds": "LHDs/train_xgboost_lhd.py"}
    if stage in scripts:
        return [["src/xgboost/" + scripts[stage]]]
    raise ValueError(f"Unknown build stage: {stage}")


def run_stage(directory: str, stage: str, root: Path = ROOT) -> None:
    workspace = Path(directory)
    if (workspace / "published.json").exists():
        return  # Clearing tasks must never mutate a published release.
    if stage == "silver":
        shutil.rmtree(workspace / "data/silver", ignore_errors=True)
        (workspace / "data/silver").mkdir()
    if stage in {"train_hospitals", "train_lhds"}:
        folder = "Hospitals" if stage == "train_hospitals" else "LHDs"
        for base in ("data/gold/xgboost", "models/xgboost"):
            shutil.rmtree(workspace / base / folder, ignore_errors=True)
    env = {**os.environ, "PYTHONPATH": str(workspace / "src"), "PYTHONUNBUFFERED": "1"}
    for command in commands(stage):
        subprocess.run([str(root / ".venv-wsl/bin/python"), *command],
                       cwd=workspace, env=env, check=True)


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def validate_run(directory: str) -> dict:
    """Reject partial training, missing entities, invalid forecasts and artifacts."""
    workspace = Path(directory)
    manifest = json.loads((workspace / "manifest.json").read_text())
    for relative, expected_hash in manifest["inputs"].items():
        if checksum(workspace / relative) != expected_hash:
            raise ValueError(f"Pinned input changed: {relative}")
    year, quarter = manifest["bhi"]["reporting_period"].split("Q")
    next_period = int(year) * 4 + int(quarter)
    for level, folder, filename, panels in [
        ("hospital", "Hospitals", "nsw_hospitals.csv", "hospital-panels"),
        ("lhd", "LHDs", "nsw_lhds.csv", "LHD-panels"),
    ]:
        refs = read_rows(workspace / "data/reference" / filename)
        expected = {r[f"bhi_{level}_id"] for r in refs}
        if not expected or len(expected) != len(refs):
            raise ValueError(f"Invalid {level} reference IDs")
        if {p.stem for p in (workspace / "data/silver" / panels).glob("*.csv")} != expected:
            raise ValueError(f"Incomplete {level} panels")
        output = workspace / "data/gold/xgboost" / folder
        report = json.loads((output / "training_report.json").read_text())
        targets = report["config"]["targets"]["columns"]
        if set(report["targets"]) != set(targets) or "pressure_index" not in targets:
            raise ValueError(f"Incomplete {level} training report")
        json.loads((output / "training_hyperparameters.json").read_text())
        for target in targets:
            metrics = report["targets"][target]
            if metrics["training_rows"] <= 0 or not math.isfinite(metrics["validation_mae"]):
                raise ValueError(f"Invalid training metrics: {level}/{target}")
            model = workspace / "models/xgboost" / folder / f"{target}.ubj"
            if not model.is_file() or model.stat().st_size == 0:
                raise ValueError(f"Missing model: {level}/{target}")
            import xgboost as xgb
            booster = xgb.Booster()
            booster.load_model(model)
        predictions = read_rows(output / "next_quarter_predictions.csv")
        if len(predictions) != len(expected) or {r[f"{level}_id"] for r in predictions} != expected:
            raise ValueError(f"Incomplete {level} predictions")
        for row in predictions:
            if int(row["year"]) * 4 + int(row["quarter"]) - 1 != next_period or not 1 <= int(row["quarter"]) <= 4:
                raise ValueError("Predictions must cover the quarter after the BHI release")
            if any(not math.isfinite(float(row[t])) for t in targets):
                raise ValueError("Nonfinite predictions")
            if not 0 <= float(row["pressure_index"]) <= 1:
                raise ValueError("Pressure predictions must be between zero and one")
    # Missing historical weather is expected with the current rolling-feed input.
    # Report it explicitly; retain blanks and coverage features rather than impute.
    weather = read_rows(workspace / "data/silver/bom/station_quarters.csv")
    if not weather:
        raise ValueError("No weather aggregates")
    mapped = {r["bom_station_id"].zfill(6) for r in read_rows(
        workspace / "data/reference/hospital_weather_mapping.csv") if r["mapping_status"] == "approved"}
    available = {r["bom_station_id"].zfill(6) for r in weather}
    validation = {"validated_at": datetime.now(timezone.utc).isoformat(),
                  "missing_weather_stations": sorted(mapped - available),
                  "incomplete_station_quarters": sum(r["incomplete_quarter"] == "True" for r in weather),
                  "weather_quarters": sorted({f'{r["year"]}Q{r["quarter"]}' for r in weather})}
    write_json(workspace / "validation.json", validation)
    return validation


def _link(target: Path, link: Path) -> None:
    temporary = link.with_name(f".{link.name}.{uuid4().hex}.tmp")
    try:
        temporary.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
        temporary.replace(link)
    finally:
        temporary.unlink(missing_ok=True)


def publish_run(directory: str, root: Path = ROOT) -> str:
    """Switch one pointer after validation; preserve all previous releases.

    First publication migrates existing directories into a bootstrap release.
    This one-time wiring uses rollback on error; later switches are atomic.
    """
    root = root.resolve()
    workspace = Path(directory).resolve()
    state = root / "data/.pipeline"
    if workspace.parent != state / "runs":
        raise ValueError("Only staged pipeline runs can be published")
    validate_run(str(workspace))
    with (state / "publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = state / "current"
        if current.is_symlink() and current.resolve() == workspace:
            return str(workspace)
        manifest = json.loads((workspace / "manifest.json").read_text())
        if current.is_symlink() and (current / "manifest.json").exists():
            previous = json.loads((current / "manifest.json").read_text())
            if manifest["created_at"] < previous["created_at"]:
                raise ValueError("Refusing to replace a newer release with an older run")
        if not current.is_symlink():
            bootstrap = state / f"bootstrap-{uuid4().hex}"
            bootstrap.mkdir()
            if any((root / name).is_symlink() for name in OUTPUTS):
                raise ValueError("Cannot migrate existing output symlinks")
            write_json(bootstrap / "bootstrap.json", {
                name: (root / name).exists() for name in OUTPUTS})
            _link(bootstrap, current)
        bootstrap = current.resolve()
        if (bootstrap / "bootstrap.json").exists():
            # The journal also lets a retry resume after worker/process death
            # between moving an original directory and creating its symlink.
            originals = json.loads((bootstrap / "bootstrap.json").read_text())
            moved = []
            try:
                for name in OUTPUTS:
                    live, saved = root / name, bootstrap / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    if live.is_symlink():
                        if live.readlink() != Path(os.path.relpath(current / name, live.parent)):
                            raise ValueError(f"Unexpected existing output symlink: {live}")
                        moved.append((live, saved, originals[name]))
                        continue
                    if live.exists():
                        live.rename(saved)
                    elif not saved.exists():
                        saved.mkdir()
                    moved.append((live, saved, originals[name]))
                    _link(current / name, live)
            except BaseException:
                for live, saved, existed in reversed(moved):
                    if live.is_symlink():
                        live.unlink()
                    if existed:
                        saved.rename(live)
                current.unlink()
                raise
        for name in OUTPUTS:
            live = root / name
            if not live.is_symlink() or live.readlink() != Path(os.path.relpath(current / name, live.parent)):
                raise ValueError(f"Output no longer points to the pipeline: {live}")
        write_json(workspace / "published.json", {"published_at": datetime.now(timezone.utc).isoformat()})
        _link(workspace, current)
    return str(workspace)
