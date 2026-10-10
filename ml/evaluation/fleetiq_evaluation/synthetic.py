"""Independent persistence evaluation for the frozen synthetic-hour anomaly forest."""

import copy

import pandas as pd
from fleetiq_registry.synthetic import SyntheticHourBundle
from fleetiq_training.anomaly import fixture, forest_score, vectors

from fleetiq_evaluation.anomaly import metrics, persistent_alerts


def evaluate_anomaly(cfg, folder, approved_hash):
    bundle = SyntheticHourBundle(folder, approved_hash)
    fresh = copy.deepcopy(cfg)
    fresh["training"]["seed"] += cfg["synthetic_hour_training"]["healthy_seed_offset"]
    fresh["anomaly_training"]["seed_offsets"] = dict(fit=0, tune=10000, evaluation=20000)
    observed = pd.concat(
        [fixture(fresh, "evaluation"), fixture(fresh, "evaluation", degrading=True)],
        ignore_index=True,
    )
    rows = vectors(observed, bundle.pre["context"])
    valid = [r for r in rows if r["vector"] is not None]
    scores = forest_score(bundle.anomaly["forest"], [r["vector"] for r in valid])
    for row, score in zip(valid, scores, strict=True):
        row["anomalous"] = bool(score > bundle.anomaly["threshold"])
    report = metrics(persistent_alerts(rows, 2, 6), observed)
    return dict(
        bundle_hash=approved_hash,
        fictional=True,
        scope="independent controlled normal/degrading fixture",
        persistence=dict(consecutive=2, cooldown_hours=6),
        metrics=report,
        retuned=False,
    )
