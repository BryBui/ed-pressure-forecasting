"""Shared configurable 0–1 pressure index for current-quarter BHI panels."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

DEFAULT_WEIGHTS = Path(__file__).with_name("pressure_indices.toml")


def load_weights(path: Path = DEFAULT_WEIGHTS) -> dict[str, float]:
    """Read unique nonnegative weights; at least one must be positive."""
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    measures = config.get("measures")
    if not isinstance(measures, list) or not measures:
        raise ValueError("Pressure configuration requires a nonempty measures array")
    weights = {}
    for measure in measures:
        if not isinstance(measure, dict):
            raise ValueError("Each pressure measure must be a table")
        column, weight = measure.get("column"), measure.get("weight")
        if not isinstance(column, str) or not column or column in weights:
            raise ValueError("Pressure measure columns must be unique nonempty strings")
        if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                or not math.isfinite(weight) or weight < 0):
            raise ValueError(f"Invalid pressure weight for {column}")
        weights[column] = float(weight)
    if not 0 < sum(weights.values()) < math.inf:
        raise ValueError("Pressure weights must have a finite positive sum")
    return weights


def calculate_pressure_index(values: Mapping[str, str], weights: Mapping[str, float]) -> float | str:
    """Invert timely-care percentages and renormalize over available measures.

    No data-dependent scaling is used, keeping scores comparable across entities
    and avoiding future-quarter information. All-missing rows remain blank.
    """
    total = available_weight = 0.0
    for column, weight in weights.items():
        value = values.get(column, "")
        if not weight or value == "":
            continue
        percentage = float(value)
        if not math.isfinite(percentage) or not 0 <= percentage <= 100:
            raise ValueError(f"Pressure input must be a percentage in [0, 100]: {column}={value}")
        total += weight * (1 - percentage / 100)
        available_weight += weight
    return total / available_weight if available_weight else ""
