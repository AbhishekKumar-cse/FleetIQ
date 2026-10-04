import hashlib
import json

import pandas as pd
import pytest
from fleetiq_data.cmapss import parse_evaluator_rul, parse_trajectory
from fleetiq_data.contracts import NASA_COLUMNS
from fleetiq_data.profile import profile


def row(unit=1, cycle=1):
    return " ".join(map(str, [unit, cycle, *range(24)])) + "   \n"


def test_explicit_native_columns_and_trailing_whitespace(tmp_path):
    path = tmp_path / "train.txt"
    path.write_text(row() + row(cycle=2))
    frame = parse_trajectory(path)
    assert tuple(frame.columns) == NASA_COLUMNS
    assert frame.unit_id.dtype == "int64"
    assert frame.cycle.tolist() == [1, 2]


@pytest.mark.parametrize(
    "text",
    ["", "1 2 3", row() + row(), row(cycle=2) + row(), row(unit=1.5), row().replace("23", "nan")],
)
def test_invalid_trajectories_rejected(tmp_path, text):
    path = tmp_path / "invalid.txt"
    path.write_text(text)
    with pytest.raises(ValueError):
        parse_trajectory(path)


@pytest.mark.parametrize(
    "values,units", [("1\n", [1, 2]), ("1\n2\n", [1, 3]), ("-1\n", [1]), ("1 2\n", [1])]
)
def test_invalid_terminal_alignment(tmp_path, values, units):
    path = tmp_path / "rul.txt"
    path.write_text(values)
    with pytest.raises(ValueError):
        parse_evaluator_rul(path, units)


def test_profile_is_repeatable_and_does_not_expose_test_targets(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    files = {
        "train_FD001.txt": row() + row(cycle=3),
        "test_FD001.txt": row(),
        "RUL_FD001.txt": "20\n",
    }
    for name, content in files.items():
        (raw / name).write_text(content)
    (raw / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    name: hashlib.sha256(content.encode()).hexdigest()
                    for name, content in files.items()
                }
            }
        )
    )
    output = tmp_path / "processed" / "profile.json"
    result = profile(raw, "FD001", output)
    assert result["train"]["per_engine"][0]["cycle_gaps"] == 1
    assert len(result["constant_training_sensors"]) == 21
    assert tuple(pd.read_parquet(output.parent / "test_observed.parquet").columns) == NASA_COLUMNS
    original = {path.name: path.read_bytes() for path in output.parent.iterdir()}
    profile(raw, "FD001", output)
    assert original == {path.name: path.read_bytes() for path in output.parent.iterdir()}
    (raw / "train_FD001.txt").write_text(row(cycle=10))
    with pytest.raises(ValueError, match="checksum"):
        profile(raw, "FD001", output)
