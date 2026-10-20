"""Rebuild and publish a complete forecast release after BHI ingestion."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.sdk import dag, get_current_context, task

from pipeline.rebuild import prepare_run, publish_run, run_stage, validate_run

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dag(
    dag_id="rebuild_forecasts",
    description="Stage, validate and publish Silver, Gold and trained models",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["bhi", "silver", "gold", "training"],
)
def rebuild_forecasts():
    @task
    def prepare() -> str:
        context = get_current_context()
        return prepare_run(PROJECT_ROOT, context["run_id"], context["dag_run"].conf)

    @task
    def refresh_references(directory: str):
        run_stage(directory, "references", PROJECT_ROOT)

    @task
    def transform_silver(directory: str):
        run_stage(directory, "silver", PROJECT_ROOT)

    @task
    def train_hospitals(directory: str):
        run_stage(directory, "train_hospitals", PROJECT_ROOT)

    @task
    def train_lhds(directory: str):
        run_stage(directory, "train_lhds", PROJECT_ROOT)

    @task
    def validate(directory: str) -> dict:
        return validate_run(directory)

    @task
    def publish(directory: str) -> str:
        return publish_run(directory, PROJECT_ROOT)

    directory = prepare()
    references = refresh_references(directory)
    silver = transform_silver(directory)
    hospitals, lhds = train_hospitals(directory), train_lhds(directory)
    validation, publication = validate(directory), publish(directory)
    references >> silver >> [hospitals, lhds]
    [hospitals, lhds] >> validation >> publication


rebuild_forecasts()
