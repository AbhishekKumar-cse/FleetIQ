"""Deterministic whole-engine partitions over NASA training observations only."""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from fleetiq_data.contracts import NASA_COLUMNS
from fleetiq_data.eda import training_group

ROOT = Path(__file__).resolve().parents[3]


def content_hash(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, allow_nan=False, default=str).encode()
    ).hexdigest()


def load_config(path=None):
    return yaml.safe_load((path or ROOT / "config/splits.yaml").read_text())


def qualified_engine(unit, *, source="NASA", subset="FD001", split="train"):
    if source != "NASA" or subset != "FD001" or split not in {"train", "test"}:
        raise ValueError("Declared benchmark source/subset/split required")
    if isinstance(unit, bool) or not str(unit).isdigit() or int(unit) < 1:
        raise ValueError("Positive native engine identity required")
    return f"{source}:{subset}:{split}:{int(unit)}"


def assert_disjoint(groups):
    seen = set()
    for name, engines in groups.items():
        if len(engines) != len(set(engines)) or seen.intersection(engines):
            raise ValueError(f"Engine leakage/duplicates in {name}")
        if any(not engine.startswith("NASA:FD001:train:") for engine in engines):
            raise ValueError("Official test/unqualified engine entered development partitions")
        seen.update(engines)


def benchmark_splits(observed, config=None, *, serial_mapping=None):
    cfg = (config or load_config())["benchmark"]
    if (cfg["source"], cfg["subset"], cfg["source_split"]) != ("NASA", "FD001", "train"):
        raise ValueError("Official test is never an input to the split builder")
    fractions = cfg["fractions"]
    if (
        set(fractions) != {"fit", "tune", "calibration"}
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value < 1
            for value in fractions.values()
        )
        or not math.isclose(sum(fractions.values()), 1)
    ):
        raise ValueError("Positive fit/tune/calibration fractions must sum to one")
    if fractions["fit"] < 0.6:
        raise ValueError("The reviewed EDA engines must remain inside fit")
    if observed.empty or set(observed.columns) != set(NASA_COLUMNS):
        raise ValueError("Training observations only; targets and provenance overrides forbidden")
    keys = observed[["unit_id", "cycle"]]
    if (
        not np.isfinite(keys.to_numpy()).all()
        or ((keys <= 0) | (keys % 1 != 0)).any().any()
        or keys.duplicated().any()
    ):
        raise ValueError("Unique positive native engine/cycle keys required")
    units = sorted(int(value) for value in keys.unit_id.unique())
    identities = {unit: qualified_engine(unit) for unit in units}
    if serial_mapping is not None:
        if set(serial_mapping) != set(identities.values()) or len(
            set(serial_mapping.values())
        ) != len(serial_mapping):
            raise ValueError("Duplicated/incomplete serial mapping")
    groups = {name: [] for name in ("fit", "tune", "calibration")}
    fit_end, tune_end = fractions["fit"] * 100, (fractions["fit"] + fractions["tune"]) * 100
    for unit in units:
        bucket = int(hashlib.sha256(str(unit).encode()).hexdigest()[:8], 16) % 100
        partition = "fit" if bucket < fit_end else "tune" if bucket < tune_end else "calibration"
        groups[partition].append(identities[unit])
    assert_disjoint(groups)
    reviewed = {identities[unit] for unit in units if training_group(str(unit))}
    if not reviewed.issubset(groups["fit"]):
        raise ValueError("EDA-reviewed engines leaked into tuning/calibration")
    if not all(groups.values()):
        raise ValueError("Insufficient independent engines for all declared partitions")
    ordered = observed.loc[:, list(NASA_COLUMNS)].sort_values(["unit_id", "cycle"])
    manifest = dict(
        version=cfg["version"],
        protocol="whole_engine_sha256_bucket",
        source="NASA",
        subset="FD001",
        source_split="train",
        fractions=fractions,
        groups=groups,
        engine_counts={name: len(values) for name, values in groups.items()},
        input_hash=content_hash(ordered.to_dict("records")),
        serial_mapping_hash=content_hash(serial_mapping or identities),
        eda_reviewed_fit_engines=sorted(reviewed),
        official_test_accessed=False,
    )
    return manifest | {"manifest_hash": content_hash(manifest)}


def load_benchmark(root=ROOT):
    path = Path(root) / "data/processed/cmapss/train.parquet"
    if path.resolve() != path.absolute():
        raise ValueError("Approved benchmark inputs cannot redirect through symlinks")
    return pd.read_parquet(path, columns=list(NASA_COLUMNS))
