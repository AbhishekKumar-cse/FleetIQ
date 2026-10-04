"""Bounded forest, same causal features and engine anchors as logistic."""

import json

import numpy as np
from fleetiq_evaluation.splits import ROOT
from sklearn.ensemble import RandomForestClassifier

from fleetiq_training.classification import predict_controlled, write_json


def fit_forest(x, y, cfg):
    settings = cfg["training"]["random_forest"]
    if not 1 <= settings["n_estimators"] <= 128 or not 1 <= settings["max_depth"] <= 10:
        raise ValueError("Bounded forest capacity required")
    if set(np.unique(y)) != {0, 1}:
        raise ValueError("Both failure classes required")
    model = RandomForestClassifier(
        **settings,
        random_state=cfg["training"]["seed"],
        n_jobs=cfg["training"]["threads"],
        class_weight=cfg["training"]["class_weight"],
    ).fit(x, y)
    trees = []
    for estimator in model.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, :]
        trees.append(
            dict(
                left=tree.children_left.tolist(),
                right=tree.children_right.tolist(),
                feature=tree.feature.tolist(),
                threshold=tree.threshold.tolist(),
                positive=(values[:, 1] / values.sum(axis=1)).tolist(),
            )
        )
    artifact = dict(model="random_forest", max_depth=settings["max_depth"], trees=trees)
    if not np.allclose(predict_controlled(artifact, x), model.predict_proba(x)[:, 1], atol=1e-10):
        raise ValueError("Controlled forest inference parity failed")
    return artifact


def compare_reports(reports):
    reports = list(reports)
    if len(reports) < 2:
        raise ValueError("At least two baseline reports required")
    reference = reports[0]["provenance"]
    if any(report["provenance"] != reference for report in reports):
        raise ValueError("Different feature/split/anchor samples invalidate comparison")
    return dict(
        provenance=reference,
        role="tune_only",
        candidates={
            report["model"]: dict(
                metrics=report["tune"],
                bootstrap=report["bootstrap"],
                inference_batch_seconds=report["measured_inference_batch_seconds"],
                manifest_hash=report["manifest_hash"],
            )
            for report in reports
        },
        final_test_accessed=False,
    )


def record_comparison():
    paths = [ROOT / f"docs/exports/{name}_tuning.json" for name in ("logistic", "random_forest")]
    comparison = compare_reports(json.loads(path.read_text()) for path in paths)
    write_json(ROOT / "docs/exports/model_baselines_comparison.json", comparison)
    return comparison
