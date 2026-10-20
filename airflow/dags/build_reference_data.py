"""Build validated LHD and hospital references from the latest BHI snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from airflow.sdk import dag, task

from reference.validate_references import build_reference_data


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_REFERENCE_ROOT = PROJECT_ROOT / "data" / "reference"


@dag(
    dag_id="build_reference_data",
    description="Extract and validate BHI LHD and hospital reference data",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    tags=["reference", "bhi", "geography", "hospitals"],
)
def build_reference_data_dag() -> None:
    @task(task_id="extract_and_validate_references", retries=2)
    def build() -> dict[str, str | int]:
        raw_root = Path(os.environ.get("RAW_DATA_ROOT", DEFAULT_RAW_ROOT))
        reference_root = Path(
            os.environ.get("REFERENCE_DATA_ROOT", DEFAULT_REFERENCE_ROOT)
        )
        return build_reference_data(raw_root, reference_root)

    build()


build_reference_data_dag()
