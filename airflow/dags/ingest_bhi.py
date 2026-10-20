"""Monthly check for newly published BHI emergency-department data."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from airflow.providers.standard.operators.trigger_dagrun import (
    TriggerDagRunOperator,
)
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowSkipException

from ingestion.bhi import ingest_latest_ed


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"


@dag(
    dag_id="ingest_bhi",
    description="Discover and ingest newly published BHI ED workbooks",
    schedule="0 9 1 * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    tags=["raw", "bhi", "healthcare", "release-watcher"],
)
def ingest_bhi() -> None:
    @task(task_id="ingest_if_new", retries=2)
    def ingest() -> dict[str, str]:
        context = get_current_context()
        run_started_at = context["dag_run"].start_date
        raw_root = Path(os.environ.get("RAW_DATA_ROOT", DEFAULT_RAW_ROOT))
        return ingest_latest_ed(raw_root, run_started_at).to_dict()

    @task(task_id="continue_if_new")
    def continue_if_new(result: dict[str, str]) -> None:
        if result["status"] != "created":
            raise AirflowSkipException("The published BHI workbook is unchanged")

    result = ingest()
    gate = continue_if_new(result)
    trigger_reference = TriggerDagRunOperator(
        task_id="build_reference_data",
        trigger_dag_id="build_reference_data",
        wait_for_completion=False,
    )
    gate >> trigger_reference


ingest_bhi()
