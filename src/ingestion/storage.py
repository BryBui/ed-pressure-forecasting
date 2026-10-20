"""Shared naming and storage helpers for immutable raw snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import re


SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_component(value: str, label: str) -> str:
    if not SAFE_COMPONENT.fullmatch(value):
        raise ValueError(
            f"{label} must contain only letters, numbers, underscores, or hyphens: "
            f"{value!r}"
        )
    return value


def format_ingestion_timestamp(value: datetime) -> str:
    """Format an aware datetime as the raw-layer UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ingested_at must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_snapshot_path(
    raw_root: Path,
    *,
    source: str,
    dataset: str,
    partitions: Mapping[str, str],
    reporting_period: str,
    ingested_at: datetime,
    extension: str,
    filename_dataset: str | None = None,
) -> Path:
    """Build a source-first, partitioned path without touching the filesystem."""
    source = _validate_component(source, "source")
    dataset = _validate_component(dataset, "dataset")
    reporting_period = _validate_component(reporting_period, "reporting_period")
    extension = _validate_component(extension.lstrip("."), "extension")
    filename_dataset = _validate_component(
        filename_dataset or dataset, "filename_dataset"
    )

    directory = Path(raw_root) / source / dataset
    for key, value in partitions.items():
        directory /= (
            f"{_validate_component(key, 'partition key')}="
            f"{_validate_component(value, 'partition value')}"
        )

    timestamp = format_ingestion_timestamp(ingested_at)
    return directory / (
        f"{filename_dataset}_{reporting_period}_{timestamp}.{extension}"
    )
