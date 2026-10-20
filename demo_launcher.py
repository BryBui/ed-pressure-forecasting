"""Run the local Airflow demo and Dash dashboard until Ctrl+C.

Usage: .venv-wsl/bin/python demo_launcher.py
"""
from __future__ import annotations

import argparse
import errno
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
DAGS = ("rebuild_forecasts", "ingest_bhi", "ingest_bom", "ingest_seifa")


def environment(root: Path, state: Path, airflow_port: int) -> dict[str, str]:
    """Use a separate local demo database, the repository DAGs and its venv."""
    home = state / "airflow"
    return {
        **os.environ,
        "PATH": str(root / ".venv-wsl/bin") + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": str(root / "src"),
        "PYTHONUNBUFFERED": "1",
        "AIRFLOW_HOME": str(home),
        "AIRFLOW_CONFIG": str(home / "airflow.cfg"),
        "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": f"sqlite:///{home / 'airflow.db'}",
        "AIRFLOW__CORE__DAGS_FOLDER": str(root / "airflow/dags"),
        "AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST": json.dumps([{
            "name": "dags-folder", "classpath": "airflow.dag_processing.bundles.local.LocalDagBundle",
            "kwargs": {},
        }]),
        "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
        "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION": "True",
        "AIRFLOW__CORE__EXECUTOR": "LocalExecutor",
        "AIRFLOW__CORE__PARALLELISM": "4",
        "AIRFLOW__API__HOST": "127.0.0.1",
        "AIRFLOW__API__PORT": str(airflow_port),
        "AIRFLOW__API__WORKERS": "1",
        "AIRFLOW__API__BASE_URL": f"http://127.0.0.1:{airflow_port}",
        "AIRFLOW__CORE__EXECUTION_API_SERVER_URL": f"http://127.0.0.1:{airflow_port}/execution/",
        "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS": "admin:admin",
        "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS": "False",
        "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE": str(home / "simple_auth_manager_passwords.json.generated"),
        "FLASK_DEBUG": "0",
    }


def check_ports(*ports: int) -> None:
    if len(set(ports)) != len(ports):
        raise ValueError("Airflow and the dashboard need different ports")
    for port in ports:
        with socket.socket() as connection:
            # Match the servers' restart behaviour: TIME_WAIT is not a listener.
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                connection.bind(("127.0.0.1", port))
            except OSError as error:
                if error.errno == errno.EADDRINUSE:
                    raise RuntimeError(f"Port {port} is unavailable; another socket is still bound. "
                                       "Wait for shutdown, stop its service or select another port") from error
                raise RuntimeError(f"Cannot check port {port}: {error}") from error


def ready(airflow_port: int, dashboard_port: int) -> bool:
    """Check actual service responses and Airflow component heartbeats."""
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{airflow_port}/api/v2/monitor/health", timeout=2) as response:
            health = json.load(response)
        if not all((health.get(name) or {}).get("status") == "healthy"
                   for name in ("metadatabase", "scheduler", "dag_processor", "triggerer")):
            return False
        with opener.open(f"http://127.0.0.1:{dashboard_port}/", timeout=2) as response:
            return response.status == 200
    except (OSError, URLError, ValueError):
        return False


def check_children(processes: dict[str, subprocess.Popen]) -> None:
    for name, process in processes.items():
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited with status {process.returncode}; check its log")


def stop_services(processes: dict[str, subprocess.Popen]) -> None:
    """Stop whole process groups, including Airflow's scheduler and workers."""
    for sig, grace in ((signal.SIGINT, 10), (signal.SIGTERM, 5), (signal.SIGKILL, 1)):
        print(f"Shutdown: sending {signal.Signals(sig).name} to demo services "
              f"(waiting up to {grace}s)...", flush=True)
        pending = []
        for process in processes.values():
            try:
                os.killpg(process.pid, sig)
                pending.append(process)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        while pending and time.monotonic() < deadline:
            remaining = []
            for process in pending:
                process.poll()  # Reap the group leader while checking descendants.
                try:
                    os.killpg(process.pid, 0)
                    remaining.append(process)
                except ProcessLookupError:
                    pass
            pending = remaining
            if pending:
                time.sleep(0.1)
        if not pending:
            break
    for process in processes.values():
        process.wait(timeout=2)


def launch(args) -> int:
    python = ROOT / ".venv-wsl/bin/python"
    if sys.platform != "linux" or not python.is_file():
        raise RuntimeError("Run in Linux/WSL with .venv-wsl/bin/python demo_launcher.py")
    state = args.state_dir.resolve()
    (state / "airflow").mkdir(parents=True, exist_ok=True)
    (state / "logs").mkdir(exist_ok=True)
    env = environment(ROOT, state, args.airflow_port)
    processes = {}
    with ExitStack() as stack:
        lock = stack.enter_context((state / "launcher.lock").open("a"))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("A demo launcher is already running or shutting down in this state directory. "
                               "Wait for 'Demo services stopped.' in its terminal before restarting") from error
        check_ports(args.airflow_port, args.dashboard_port)
        airflow_log = stack.enter_context((state / "logs/airflow.log").open("a"))
        dashboard_log = stack.enter_context((state / "logs/dashboard.log").open("a"))
        dependency_check = subprocess.run([
            str(python), "-c",
            "import importlib.util, sys; names = ['airflow', 'dash', 'dash_mantine_components', 'geopandas', 'xgboost', 'pipeline']; "
            "missing = [name for name in names if importlib.util.find_spec(name) is None]; "
            "sys.exit('Missing dependencies: ' + ', '.join(missing) if missing else 0)",
        ], cwd=ROOT, env=env, capture_output=True, text=True)
        if dependency_check.returncode:
            raise RuntimeError(dependency_check.stderr.strip())
        try:
            processes["Airflow"] = subprocess.Popen(
                [str(python), "-m", "airflow", "standalone"], cwd=ROOT, env=env,
                stdout=airflow_log, stderr=subprocess.STDOUT, start_new_session=True)
            processes["Dashboard"] = subprocess.Popen(
                [str(python), "-m", "flask", "--app", "app:server", "run", "--host", "127.0.0.1",
                 "--port", str(args.dashboard_port), "--no-reload", "--no-debugger"],
                cwd=ROOT / "dashboard-dash-mantine", env=env,
                stdout=dashboard_log, stderr=subprocess.STDOUT, start_new_session=True)
            print(f"Starting Airflow and dashboard. Logs: {state / 'logs'}", flush=True)
            deadline = time.monotonic() + args.startup_timeout
            while not ready(args.airflow_port, args.dashboard_port):
                check_children(processes)
                if time.monotonic() >= deadline:
                    raise RuntimeError("Services did not become healthy before the startup timeout")
                time.sleep(1)
            pending = [] if args.no_unpause else list(DAGS)
            while pending:
                check_children(processes)
                result = subprocess.run(
                    [str(python), "-m", "airflow", "dags", "unpause", pending[0], "--yes"],
                    cwd=ROOT, env=env, stdout=airflow_log, stderr=subprocess.STDOUT, timeout=20)
                if result.returncode == 0:
                    pending.pop(0)
                elif time.monotonic() >= deadline:
                    raise RuntimeError(f"Could not enable DAG {pending[0]}; check the Airflow log")
                else:
                    time.sleep(1)
            print(f"Dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
            print(f"Airflow:   http://127.0.0.1:{args.airflow_port} (user: admin)", flush=True)
            print(f"Airflow password file: {env['AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE']}", flush=True)
            print("Running continuously on the existing schedules. Press Ctrl+C to stop both services.", flush=True)
            failures = 0
            while True:
                check_children(processes)
                failures = 0 if ready(args.airflow_port, args.dashboard_port) else failures + 1
                if failures >= 3:
                    raise RuntimeError("Service health checks failed three times; check the logs")
                time.sleep(5)
        finally:
            print("Stopping demo services...", flush=True)
            # A second Ctrl+C must not interrupt cleanup and orphan workers.
            handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                stop_services(processes)
                print("Demo services stopped.", flush=True)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airflow-port", type=int, default=8080)
    parser.add_argument("--dashboard-port", type=int, default=8050)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".demo")
    parser.add_argument("--startup-timeout", type=float, default=180)
    parser.add_argument("--no-unpause", action="store_true",
                        help="Leave DAG pause states unchanged; useful for a service-only smoke test")
    args = parser.parse_args(argv)
    if any(not 1 <= port <= 65535 for port in (args.airflow_port, args.dashboard_port)):
        parser.error("Ports must be between 1 and 65535")
    if args.startup_timeout <= 0:
        parser.error("Startup timeout must be positive")
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    original = signal.signal(signal.SIGTERM, interrupted)
    try:
        return launch(args)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Demo launcher: {error}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, original)


if __name__ == "__main__":
    raise SystemExit(main())
