"""Demo configuration, startup failure cleanup and real process shutdown."""
import argparse
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("demo_launcher", ROOT / "demo_launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class DemoLauncherTests(unittest.TestCase):
    def test_environment_isolated_database_and_venv_commands(self):
        with patch.dict(os.environ, {"AIRFLOW_HOME": "/unrelated", "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": "external"}):
            env = launcher.environment(Path("/project"), Path("/demo"), 18080)
        self.assertEqual(env["AIRFLOW_HOME"], "/demo/airflow")
        self.assertEqual(env["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"], "sqlite:////demo/airflow/airflow.db")
        self.assertTrue(env["PATH"].startswith("/project/.venv-wsl/bin" + os.pathsep))
        self.assertEqual(env["PYTHONPATH"], "/project/src")
        self.assertEqual(env["AIRFLOW__API__HOST"], "127.0.0.1")
        self.assertEqual(env["AIRFLOW__CORE__EXECUTION_API_SERVER_URL"], "http://127.0.0.1:18080/execution/")
        self.assertEqual(env["AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION"], "True")
        self.assertEqual(json.loads(env["AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST"])[0]["name"], "dags-folder")

    def test_busy_or_duplicate_ports_report_clear_error(self):
        with self.assertRaisesRegex(ValueError, "different ports"):
            launcher.check_ports(8050, 8050)
        with patch.object(launcher.socket, "socket") as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError(errno.EADDRINUSE, "busy")
            with self.assertRaisesRegex(RuntimeError, "8050 is unavailable"):
                launcher.check_ports(8050)

    def test_port_permission_error_is_not_reported_as_occupied(self):
        with patch.object(launcher.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = PermissionError(errno.EACCES, 'denied')
            with self.assertRaisesRegex(RuntimeError, 'Cannot check port 8080'):
                launcher.check_ports(8080)

    def test_launcher_lock_is_checked_before_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root, args = self.fixture(directory)
            with patch.object(launcher, 'ROOT', root), \
                 patch.object(launcher.fcntl, 'flock', side_effect=BlockingIOError), \
                 patch.object(launcher, 'check_ports') as ports:
                with self.assertRaisesRegex(RuntimeError, 'running or shutting down'):
                    launcher.launch(args)
                ports.assert_not_called()

    def test_recently_closed_connection_allows_restart_but_listener_does_not(self):
        with launcher.socket.socket() as server:
            server.setsockopt(launcher.socket.SOL_SOCKET, launcher.socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', 0))
            port = server.getsockname()[1]
            server.listen()
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                launcher.check_ports(port)
            with launcher.socket.create_connection(('127.0.0.1', port)) as client:
                accepted, _ = server.accept()
                accepted.close()
                self.assertEqual(client.recv(1), b'')
        launcher.check_ports(port)

    def fixture(self, directory):
        root = Path(directory)
        python = root / ".venv-wsl/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        return root, argparse.Namespace(state_dir=root / ".demo", airflow_port=18080,
            dashboard_port=18050, startup_timeout=20, no_unpause=False)

    def test_failed_dashboard_start_stops_airflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root, args = self.fixture(directory)
            airflow = Mock()
            with patch.object(launcher, "ROOT", root), patch.object(launcher, "check_ports"), \
                 patch.object(launcher.subprocess, "run", return_value=Mock(returncode=0)), \
                 patch.object(launcher.subprocess, "Popen", side_effect=[airflow, OSError("start failed")]), \
                 patch.object(launcher, "stop_services") as stop:
                with self.assertRaisesRegex(OSError, "start failed"):
                    launcher.launch(args)
                stop.assert_called_once_with({"Airflow": airflow})

    def test_downstream_dag_enabled_before_ingestion_and_both_services_supervised(self):
        with tempfile.TemporaryDirectory() as directory:
            root, args = self.fixture(directory)
            with patch.object(launcher, "ROOT", root), patch.object(launcher, "check_ports"), \
                 patch.object(launcher.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                 patch.object(launcher.subprocess, "Popen") as start, \
                 patch.object(launcher, "ready", return_value=True), \
                 patch.object(launcher, "check_children", side_effect=[None] * 4 + [RuntimeError("stopped")]), \
                 patch.object(launcher, "stop_services") as stop:
                with self.assertRaisesRegex(RuntimeError, "stopped"):
                    launcher.launch(args)
                enabled = [call.args[0][-2] for call in run.call_args_list[1:]]
                self.assertEqual(enabled, list(launcher.DAGS))
                self.assertEqual(start.call_count, 2)
                self.assertTrue(all(call.kwargs["start_new_session"] for call in start.call_args_list))
                self.assertEqual(set(stop.call_args.args[0]), {"Airflow", "Dashboard"})

    def test_real_process_groups_stop_cleanly(self):
        processes = {name: subprocess.Popen(
            [str(ROOT / ".venv-wsl/bin/python"), "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            for name in ("Airflow", "Dashboard")}
        try:
            launcher.stop_services(processes)
            self.assertTrue(all(process.poll() is not None for process in processes.values()))
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
