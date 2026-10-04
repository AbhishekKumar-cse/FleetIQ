from pathlib import Path

import pandas as pd
import pytest
from fleetiq_data import eda

ROOT = Path(__file__).resolve().parents[2]


def test_access_allowlist_and_training_groups(monkeypatch):
    original = pd.read_parquet
    seen = []

    def guarded(path, **kwargs):
        relative = str(Path(path).relative_to(ROOT / "data"))
        assert relative in eda.ALLOWED
        assert "test" not in relative and "evaluator" not in relative and "RUL_" not in relative
        seen.append(relative)
        return original(path, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", guarded)
    for track, group in [("cmapss_benchmark", "unit_id"), ("synthetic_engine_demo", "aircraft_id")]:
        frame = eda.load_training(ROOT / "data", track)
        assert len(frame) > 0 and all(
            eda.training_group(identifier) for identifier in frame[group].unique()
        )
        assert not any("rul" in column.lower() or "latent" in column for column in frame.columns)
        eda.training_labels(ROOT / "data", frame, track)
    assert set(seen) == eda.ALLOWED
    with pytest.raises(ValueError):
        eda._read(ROOT / "data", "raw/cmapss/RUL_FD001.txt")
    with pytest.raises(ValueError):
        eda.load_training(ROOT / "data", "test")


def test_symlink_cannot_redirect_training_input(tmp_path):
    approved = tmp_path / "processed/cmapss"
    approved.mkdir(parents=True)
    target = tmp_path / "test.parquet"
    target.write_bytes(b"not training")
    (approved / "train.parquet").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        eda.load_training(tmp_path, "cmapss_benchmark")
