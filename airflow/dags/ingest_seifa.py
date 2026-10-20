"""Daily watcher for a newly published ABS SA1 SEIFA reference period."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from airflow.sdk import dag, get_current_context, task

from ingestion.seifa import ingest_latest_sa1_if_new_period
from ingestion.SA1_shapefile import ingest_sa1_shapefile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"


@dag(
    dag_id="ingest_seifa",
    description="Ingest new ABS SA1 SEIFA data and matching Census boundaries",
    schedule="0 9 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    tags=["raw", "abs", "seifa", "release-watcher"],
)
def ingest_seifa() -> None:
    @task(task_id="ingest_if_new_period", retries=2)
    def ingest() -> dict[str, str]:
        context = get_current_context()
        raw_root = Path(os.environ.get("RAW_DATA_ROOT", DEFAULT_RAW_ROOT))
        result = ingest_latest_sa1_if_new_period(
            raw_root,
            context["dag_run"].start_date,
        )
        return result.to_dict()

    @task(task_id="ingest_sa1_shapefile", retries=2)
    def ingest_boundaries(seifa_result: dict[str, str]) -> dict[str, str]:
        context = get_current_context()
        raw_root = Path(os.environ.get("RAW_DATA_ROOT", DEFAULT_RAW_ROOT))
        return ingest_sa1_shapefile(
            raw_root,
            context["dag_run"].start_date,
            int(seifa_result["reporting_period"]),
        ).to_dict()

    ingest_boundaries(ingest())


ingest_seifa()
