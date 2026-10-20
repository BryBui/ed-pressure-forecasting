"""Collect mapped BoM observations from the sanctioned public data feed."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from airflow.sdk import dag, get_current_context, task

from ingestion.bom import (
    ingest_public_observations,
    load_approved_weather_mapping,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING = (
    PROJECT_ROOT / "data" / "reference" / "hospital_weather_mapping.csv"
)
DEFAULT_HOSPITALS = PROJECT_ROOT / "data" / "reference" / "nsw_hospitals.csv"
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"


@dag(
    dag_id="ingest_bom",
    description="Collect rolling BoM station observations for mapped hospitals",
    schedule="0 9 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    tags=["raw", "bom", "weather", "public-feed"],
)
def ingest_bom() -> None:
    @task(task_id="ingest_public_observations", retries=2)
    def ingest() -> dict[str, object]:
        context = get_current_context()
        mapping_path = Path(
            os.environ.get("HOSPITAL_WEATHER_MAPPING", DEFAULT_MAPPING)
        )
        hospitals_path = Path(
            os.environ.get("NSW_HOSPITAL_REFERENCE", DEFAULT_HOSPITALS)
        )
        raw_root = Path(os.environ.get("RAW_DATA_ROOT", DEFAULT_RAW_ROOT))
        mapping_rows = load_approved_weather_mapping(
            mapping_path, hospitals_path
        )
        result = ingest_public_observations(
            raw_root,
            context["dag_run"].start_date,
            mapping_rows,
            max_workers=int(os.environ.get("BOM_DOWNLOAD_WORKERS", "4")),
        )
        return result.to_dict()

    ingest()


ingest_bom()
