"""Strict native-cycle parsing; official terminal labels are evaluator-only."""

from pathlib import Path

import numpy as np
import pandas as pd

from fleetiq_data.contracts import NASA_COLUMNS


def parse_trajectory(path: Path) -> pd.DataFrame:
    rows = []
    last_cycle: dict[int, int] = {}
    for number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != len(NASA_COLUMNS):
            raise ValueError(f"row {number}: expected 26 columns")
        try:
            values = [float(value) for value in fields]
        except ValueError as error:
            raise ValueError(f"row {number}: invalid number") from error
        if not np.isfinite(values).all():
            raise ValueError(f"row {number}: nonfinite value")
        if any(value <= 0 or not value.is_integer() for value in values[:2]):
            raise ValueError(f"row {number}: unit and cycle must be positive integers")
        unit, cycle = map(int, values[:2])
        if cycle <= last_cycle.get(unit, 0):
            raise ValueError(f"row {number}: duplicate or nonmonotonic cycle")
        last_cycle[unit] = cycle
        rows.append(values)
    if not rows:
        raise ValueError("empty trajectory")
    frame = pd.DataFrame(rows, columns=NASA_COLUMNS)
    return frame.astype({"unit_id": "int64", "cycle": "int64"})


def parse_evaluator_rul(path: Path, unit_ids: list[int]) -> pd.DataFrame:
    """NASA's vector order is ascending consecutive unit ID, starting at one."""
    if sorted(unit_ids) != list(range(1, len(unit_ids) + 1)):
        raise ValueError("official RUL requires consecutive unit IDs")
    values = []
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 1:
            raise ValueError("RUL requires one integer per row")
        value = float(fields[0])
        if not np.isfinite(value) or value < 0 or not value.is_integer():
            raise ValueError("RUL must be a nonnegative integer")
        values.append(int(value))
    if len(values) != len(unit_ids):
        raise ValueError("terminal target count does not match test engines")
    return pd.DataFrame({"unit_id": sorted(unit_ids), "rul_cycles": values})
