"""Causal multi-scale engine features; endpoint outcomes never enter extraction."""

import hashlib
from zipfile import ZipFile

import numpy as np
import pandas as pd
from fleetiq_data.cmapss import parse_trajectory

from fleetiq_training.classification import file_hash


def development(root, cfg):
    """Extract only training members from the already verified NASA archive."""
    archive = root / "data/raw/cmapss/CMAPSSData.zip"
    expected = "74bef434a34db25c7bf72e668ea4cd52afe5f2cf8e44367c55a82bfd91a5a34f"
    if file_hash(archive) != expected:
        raise ValueError("NASA archive integrity mismatch")
    parts, hashes = [], {}
    with ZipFile(archive) as bundle:
        for subset in cfg["development_subsets"]:
            if subset not in {"FD001", "FD003"}:
                raise ValueError("Only predeclared training subsets supported")
            name = f"train_{subset}.txt"
            path = root / "data/raw/cmapss" / name
            if not path.exists():
                path.write_bytes(bundle.read(name))
            hashes[name] = file_hash(path)
            frame = parse_trajectory(path)
            frame["subset"] = subset
            frame["stream"] = [f"NASA:{subset}:train:{u}" for u in frame.unit_id]
            frame["role"] = ""
            engines = sorted(frame.unit_id.unique())
            if len(engines) != 100:
                raise ValueError("Expected 100 independent training engines per subset")
            # Outcome-independent membership, frozen before any FD003 outcome inspection.
            ordered = np.random.default_rng(cfg["seed"]).permutation(engines)
            cursor = 0
            for role, size in cfg["partition_per_subset"].items():
                frame.loc[frame.unit_id.isin(ordered[cursor : cursor + size]), "role"] = role
                cursor += size
            if cursor != len(engines) or (frame.role == "").any():
                raise ValueError("Exhaustive disjoint engine partition required")
            parts.append(frame)
    return pd.concat(parts, ignore_index=True), hashes


def engine_features(frame, *, enhanced=True):
    """Only trailing observations and the first 20 already-observed cycles are used."""
    if len(frame) < 30 or not np.array_equal(frame.cycle, np.arange(1, len(frame) + 1)):
        raise ValueError("At least 30 contiguous native cycles required")
    columns = [f"sensor_{i}" for i in range(1, 22)]
    y = frame[columns].to_numpy(dtype=float)
    if not np.isfinite(y).all():
        raise ValueError("Finite observed NASA sensor values required")
    n = len(y)
    t = np.arange(n, dtype=float)
    cumulative = np.vstack([np.zeros((1, 21)), np.cumsum(y, axis=0)])
    weighted = np.vstack([np.zeros((1, 21)), np.cumsum(y * t[:, None], axis=0)])
    body = {}
    windows = (5, 15, 30, 60) if enhanced else (30,)
    for window in windows:
        start = np.maximum(0, np.arange(n) - window + 1)
        count = np.minimum(np.arange(n) + 1, window)
        sums = cumulative[np.arange(n) + 1] - cumulative[start]
        mean = sums / count[:, None]
        # Population standard deviation computed on trailing values, avoiding cancellation.
        std = pd.DataFrame(y).rolling(window, min_periods=1).std(ddof=0).to_numpy()
        iy = weighted[np.arange(n) + 1] - weighted[start]
        mean_i = (t + start) / 2
        denominator = count * (count * count - 1) / 12
        slope = np.divide(
            iy - mean_i[:, None] * sums,
            denominator[:, None],
            out=np.zeros_like(iy),
            where=denominator[:, None] > 0,
        )
        for j, sensor in enumerate(columns):
            for name, value in (("mean", mean), ("std", std), ("slope", slope)):
                body[f"{sensor}.w{window}.{name}"] = value[:, j]
        if enhanced:
            body[f"history.w{window}.count"] = count
    ewma = pd.DataFrame(y).ewm(alpha=0.2, adjust=False).mean().to_numpy()
    early_mean, early_std = y[:20].mean(axis=0), y[:20].std(axis=0)
    for j, sensor in enumerate(columns):
        body[f"{sensor}.ewma"] = ewma[:, j]
        if enhanced:
            body[f"{sensor}.current"] = y[:, j]
            body[f"{sensor}.early_offset"] = y[:, j] - early_mean[j]
            body[f"{sensor}.early_z"] = np.clip(
                (ewma[:, j] - early_mean[j]) / max(early_std[j], 1e-6), -20, 20
            )
            body[f"{sensor}.mean_shift_5_60"] = (
                body[f"{sensor}.w5.mean"] - body[f"{sensor}.w60.mean"]
            )
            body[f"{sensor}.slope_change_15_60"] = (
                body[f"{sensor}.w15.slope"] - body[f"{sensor}.w60.slope"]
            )
    body["observed_age_cycles"] = frame.cycle.to_numpy()
    for name in ("setting_1", "setting_2", "setting_3"):
        body[name] = frame[name].to_numpy()
    return pd.DataFrame(body, index=frame.index).loc[frame.cycle >= 30]


def build_table(frame, cfg, *, enhanced=True, outcomes=True):
    parts = []
    for stream, group in frame.groupby("stream", sort=True):
        group = group.sort_values("cycle")
        features = engine_features(group, enhanced=enhanced)
        keys = group.loc[features.index, ["stream", "cycle", "role"]].copy()
        if outcomes:
            keys["event_cycle"] = int(group.cycle.max())
            keys["rul_cycles"] = keys.event_cycle - keys.cycle
            keys["failure_within_horizon"] = (
                (keys.rul_cycles > 0) & (keys.rul_cycles <= cfg["horizon_cycles"])
            ).astype(int)
            keys = keys.loc[keys.rul_cycles > 0]
        parts.append(pd.concat([keys, features.loc[keys.index]], axis=1))
    return pd.concat(parts, ignore_index=True)


def endpoint_indices(rows, cfg):
    """Outcome-independent truncations drawn separately for every engine."""
    selected = []
    for stream, group in rows.groupby("stream", sort=True):
        seed = int(hashlib.sha256(f"{cfg['seed']}:{stream}".encode()).hexdigest()[:16], 16)
        generator = np.random.default_rng(seed)
        selected.extend(
            generator.choice(
                group.index.to_numpy(),
                min(cfg["endpoint_draws_per_engine"], len(group)),
                replace=False,
            ).tolist()
        )
    return sorted(selected)
