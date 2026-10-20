"""Offline release lifecycle, failure isolation and real subprocess/model checks."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import xgboost as xgb

from pipeline import rebuild


def csv_file(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        booster = xgb.train({"max_depth": 1, "nthread": 1},
                            xgb.DMatrix(np.array([[0.0], [1.0]]), label=[0.2, 0.4]),
                            num_boost_round=1)
        cls.model = booster.save_raw(raw_format="ubj")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Ignore developer-specific raw/reference overrides in offline fixtures.
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("RAW_DATA_ROOT", None)
        os.environ.pop("REFERENCE_DATA_ROOT", None)

    def inputs(self):
        source = self.root / "data/raw/bhi/ed_performance/year=2026/quarter=Q2/ed_performance_2026Q2_20261020T010000Z.xlsx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"immutable workbook")
        seifa = self.root / "data/raw/abs/seifa/census_year=2021/seifa_2021_20261020T010000Z.xlsx"
        seifa.parent.mkdir(parents=True)
        seifa.write_bytes(b"seifa")
        weather = self.root / "data/raw/bom/station_observations/station_id=000001/snapshot.json"
        weather.parent.mkdir(parents=True)
        weather.write_text('{"observations": {"data": []}}')
        (self.root / "data/reference").mkdir()
        (self.root / "data/reference/original.csv").write_text("preserve")
        (self.root / "data/reference/nsw_sa1_boundaries.geojson").write_text("{}")
        (self.root / "src").mkdir()
        (self.root / "src/example.py").write_text("print('pinned')")
        return {"path": str(source), "sha256": rebuild.checksum(source), "reporting_period": "2026Q2"}

    def release(self, name="run", created="2026-10-20T00:00:00+00:00"):
        workspace = self.root / "data/.pipeline/runs" / name
        workspace.mkdir(parents=True)
        rebuild.write_json(workspace / "manifest.json", {
            "bhi": {"reporting_period": "2026Q2"}, "created_at": created, "inputs": {}})
        for level, folder, reference, panels in [
            ("hospital", "Hospitals", "nsw_hospitals.csv", "hospital-panels"),
            ("lhd", "LHDs", "nsw_lhds.csv", "LHD-panels"),
        ]:
            csv_file(workspace / "data/reference" / reference, [{f"bhi_{level}_id": "A"}])
            csv_file(workspace / "data/silver" / panels / "A.csv", [{"year": 2026, "quarter": 2}])
            output = workspace / "data/gold/xgboost" / folder
            output.mkdir(parents=True)
            rebuild.write_json(output / "training_report.json", {
                "config": {"targets": {"columns": ["pressure_index"]}},
                "targets": {"pressure_index": {"training_rows": 10, "validation_mae": 0.1}}})
            rebuild.write_json(output / "training_hyperparameters.json", {"targets": {"pressure_index": {}}})
            csv_file(output / "next_quarter_predictions.csv", [
                {f"{level}_id": "A", "year": 2026, "quarter": 3, "pressure_index": 0.4}])
            model = workspace / "models/xgboost" / folder / "pressure_index.ubj"
            model.parent.mkdir(parents=True)
            model.write_bytes(self.model)
        csv_file(workspace / "data/reference/hospital_weather_mapping.csv", [
            {"bom_station_id": "000001", "mapping_status": "approved"}])
        csv_file(workspace / "data/silver/bom/station_quarters.csv", [
            {"bom_station_id": "000002", "year": 2026, "quarter": 4, "incomplete_quarter": "True"}])
        return workspace

    def test_snapshot_retries_pin_weather_code_and_seifa(self):
        conf = self.inputs()
        directory = Path(rebuild.prepare_run(self.root, "manual/run", conf))
        original_manifest = (directory / "manifest.json").read_text()
        (self.root / "src/example.py").write_text("changed")
        (self.root / "data/raw/bom/station_observations/new.json").write_text("new weather")
        self.assertEqual(rebuild.prepare_run(self.root, "manual/run", conf), str(directory))
        self.assertEqual((directory / "manifest.json").read_text(), original_manifest)
        self.assertEqual((directory / "src/example.py").read_text(), "print('pinned')")
        self.assertFalse((directory / "data/raw/bom/station_observations/new.json").exists())
        self.assertEqual((self.root / "data/reference/original.csv").read_text(), "preserve")

    def test_bad_checksum_and_missing_inputs_never_publish(self):
        conf = self.inputs()
        with self.assertRaisesRegex(ValueError, "checksum"):
            rebuild.prepare_run(self.root, "bad", {**conf, "sha256": "wrong"})
        with self.assertRaisesRegex(ValueError, "reporting_period"):
            rebuild.prepare_run(self.root, "bad-period", {**conf, "reporting_period": "2026Q3"})
        for file in (self.root / "data/raw/abs/seifa").rglob("*.xlsx"):
            file.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "SEIFA"):
            rebuild.prepare_run(self.root, "missing", conf)
        self.assertFalse((self.root / "data/.pipeline/current").exists())

    def test_validation_and_atomic_replacement_preserve_previous_and_raw(self):
        conf = self.inputs()
        old = self.root / "data/silver/obsolete.csv"
        old.parent.mkdir()
        old.write_text("old output")
        first = self.release()
        result = rebuild.validate_run(str(first))
        self.assertEqual(result["missing_weather_stations"], ["000001"])
        self.assertEqual(result["incomplete_station_quarters"], 1)
        rebuild.publish_run(str(first), self.root)
        self.assertFalse(old.exists())
        bootstrap = next((self.root / "data/.pipeline").glob("bootstrap-*"))
        self.assertEqual((bootstrap / "data/silver/obsolete.csv").read_text(), "old output")
        self.assertFalse((self.root / "data/reference").is_symlink())
        self.assertEqual((self.root / "data/reference/original.csv").read_text(), "preserve")
        self.assertEqual(rebuild.checksum(Path(conf["path"])), conf["sha256"])
        second = self.release("second", "2026-10-21T00:00:00+00:00")
        rebuild.publish_run(str(second), self.root)
        self.assertEqual((self.root / "data/silver").resolve(), second / "data/silver")
        self.assertTrue((first / "data/silver/hospital-panels/A.csv").exists())
        self.assertEqual(rebuild.publish_run(str(second), self.root), str(second))
        with self.assertRaisesRegex(ValueError, "newer release"):
            rebuild.publish_run(str(first), self.root)

    def test_failed_publish_rolls_back_initial_directory_migration(self):
        self.inputs()
        workspace = self.release()
        original_link = rebuild._link
        def fail_models(target, link):
            if link == self.root / "models":
                raise OSError("simulated failure")
            original_link(target, link)
        with patch.object(rebuild, "_link", side_effect=fail_models):
            with self.assertRaisesRegex(OSError, "simulated failure"):
                rebuild.publish_run(str(workspace), self.root)
        self.assertFalse((self.root / "data/reference").is_symlink())
        self.assertEqual((self.root / "data/reference/original.csv").read_text(), "preserve")
        self.assertFalse((self.root / "data/.pipeline/current").is_symlink())
        rebuild.publish_run(str(workspace), self.root)

    def test_invalid_forecast_or_model_keeps_live_release(self):
        first = self.release()
        rebuild.publish_run(str(first), self.root)
        invalid = self.release("invalid", "2026-10-21T00:00:00+00:00")
        prediction = invalid / "data/gold/xgboost/Hospitals/next_quarter_predictions.csv"
        prediction.write_text("hospital_id,year,quarter,pressure_index\nA,2026,4,nan\n")
        with self.assertRaises(ValueError):
            rebuild.publish_run(str(invalid), self.root)
        self.assertEqual((self.root / "data/.pipeline/current").resolve(), first)
        prediction.write_text("hospital_id,year,quarter,pressure_index\nA,2026,3,0.4\n")
        (invalid / "models/xgboost/Hospitals/pressure_index.ubj").write_bytes(b"bad model")
        with self.assertRaises(xgb.core.XGBoostError):
            rebuild.publish_run(str(invalid), self.root)
        self.assertEqual((self.root / "data/.pipeline/current").resolve(), first)

    def test_first_publication_resumes_after_interrupted_migration(self):
        self.inputs()
        old = self.root / "data/silver/old.csv"
        old.parent.mkdir()
        old.write_text("original")
        workspace = self.release()
        state = self.root / "data/.pipeline"
        bootstrap = state / "bootstrap-interrupted"
        bootstrap.mkdir()
        rebuild.write_json(bootstrap / "bootstrap.json", {
            name: (self.root / name).exists() for name in rebuild.OUTPUTS})
        rebuild._link(bootstrap, state / "current")
        (bootstrap / "data").mkdir()
        (self.root / "data/silver").rename(bootstrap / "data/silver")
        # Simulate a stopped worker before it could install the live symlink.
        rebuild.publish_run(str(workspace), self.root)
        self.assertEqual((state / "current").resolve(), workspace)
        self.assertEqual((bootstrap / "data/silver/old.csv").read_text(), "original")

    def test_subprocess_failure_stops_stage_and_published_runs_are_immutable(self):
        workspace = self.release()
        with patch.object(rebuild.subprocess, "run", side_effect=rebuild.subprocess.CalledProcessError(1, "test")) as run:
            with self.assertRaises(rebuild.subprocess.CalledProcessError):
                rebuild.run_stage(str(workspace), "references", self.root)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs["cwd"], workspace)
        rebuild.publish_run(str(workspace), self.root)
        with patch.object(rebuild.subprocess, "run") as run:
            rebuild.run_stage(str(workspace), "silver", self.root)
            run.assert_not_called()

    def test_changed_pinned_input_blocks_publication(self):
        workspace = self.release()
        pinned = workspace / "pinned-source"
        pinned.write_text("original")
        manifest = json.loads((workspace / "manifest.json").read_text())
        manifest["inputs"] = {"pinned-source": rebuild.checksum(pinned)}
        rebuild.write_json(workspace / "manifest.json", manifest)
        pinned.write_text("changed")
        with self.assertRaisesRegex(ValueError, "Pinned input changed"):
            rebuild.publish_run(str(workspace), self.root)
        self.assertFalse((self.root / "data/.pipeline/current").exists())


class AirflowGraphTests(unittest.TestCase):
    def test_dags_import_and_publish_waits_for_both_training_branches(self):
        try:
            from airflow.dag_processing.dagbag import DagBag
        except ImportError:
            self.skipTest("Airflow is an optional Linux runtime")
        bag = DagBag(dag_folder=rebuild.ROOT / "airflow/dags")
        self.assertEqual(bag.import_errors, {})
        dag = bag.dags["rebuild_forecasts"]
        self.assertEqual(dag.max_active_runs, 1)
        self.assertEqual(dag.get_task("validate").upstream_task_ids,
                         {"prepare", "train_hospitals", "train_lhds"})
        self.assertEqual(dag.get_task("publish").upstream_task_ids, {"prepare", "validate"})
        trigger = bag.dags["ingest_bhi"].get_task("rebuild_forecasts")
        self.assertTrue(trigger.skip_when_already_exists)
        self.assertTrue(trigger.fail_when_dag_is_paused)
        self.assertEqual(trigger.trigger_dag_id, "rebuild_forecasts")
        from airflow.sdk.exceptions import AirflowSkipException
        gate = bag.dags["ingest_bhi"].get_task("continue_if_new")
        with self.assertRaises(AirflowSkipException):
            gate.python_callable({"status": "unchanged"})
        gate.python_callable({"status": "created"})


if __name__ == "__main__":
    unittest.main()
