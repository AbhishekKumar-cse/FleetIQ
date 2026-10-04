"""Bounded CPU histogram search; tuning is the only early-stop/selection role."""

import json
from itertools import product

import numpy as np
from fleetiq_evaluation.splits import ROOT, content_hash
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

from fleetiq_training.classification import load_model, write_json
from fleetiq_training.random_forest import compare_reports


def fit_xgboost(x, y, tune_x, tune_y, cfg):
    grid = cfg["training"]["xgboost"]
    if set(np.unique(y)) != {0, 1} or set(np.unique(tune_y)) != {0, 1}:
        raise ValueError("Both naturally sampled fit/tune outcomes required")
    if not 1 <= grid["n_estimators"] <= 300 or not 1 <= grid["early_stopping_rounds"] <= 40:
        raise ValueError("Bounded boosting budget required")
    candidates = list(product(grid["max_depth"], grid["learning_rate"]))
    if not 1 <= len(candidates) <= 6 or any(
        not 1 <= depth <= 6 or not 0 < rate <= 0.2 for depth, rate in candidates
    ):
        raise ValueError("Bounded depth/rate search required")
    outcomes = []
    best = None
    for depth, rate in candidates:
        model = XGBClassifier(
            n_estimators=grid["n_estimators"],
            max_depth=depth,
            learning_rate=rate,
            early_stopping_rounds=grid["early_stopping_rounds"],
            tree_method="hist",
            device="cpu",
            n_jobs=cfg["training"]["threads"],
            random_state=cfg["training"]["seed"],
            eval_metric="logloss",
            subsample=1,
            colsample_bytree=1,
            scale_pos_weight=float(np.sum(y == 0) / np.sum(y == 1))
            if cfg["training"]["class_weight"] == "balanced"
            else 1,
        )
        model.fit(x, y, eval_set=[(tune_x, tune_y)], verbose=False)
        score = float(average_precision_score(tune_y, model.predict_proba(tune_x)[:, 1]))
        settings = dict(
            max_depth=depth,
            learning_rate=rate,
            best_iteration=int(model.best_iteration),
            average_precision=score,
        )
        outcomes.append(settings)
        key = (score, -depth, -rate)
        if best is None or key > best[0]:
            best = (key, model, settings)
    settings = dict(
        selected=best[2],
        search=outcomes,
        tree_method="hist",
        device="cpu",
        n_estimators=grid["n_estimators"],
        early_stopping_role="tune_only",
    )
    return best[1], settings


def select_candidate(reports, minimum_gain):
    comparison = compare_reports(reports)
    candidates = comparison["candidates"]
    if set(candidates) != {"logistic", "random_forest", "xgboost"}:
        raise ValueError("All three reference runs required before selection")
    logistic = candidates["logistic"]["metrics"]
    chosen = "logistic"
    qualified = []
    for name in ("random_forest", "xgboost"):
        result = candidates[name]["metrics"]
        if (
            result["event_recall"] >= logistic["event_recall"]
            and result["average_precision"] >= logistic["average_precision"] + minimum_gain
        ):
            qualified.append((result["average_precision"], name))
    if qualified:
        chosen = max(qualified)[1]
    selection = dict(
        version="failure-selection-v1",
        role="tune_only",
        chosen=chosen,
        threshold=candidates[chosen]["metrics"]["threshold"],
        minimum_complex_model_ap_gain=minimum_gain,
        candidate_manifest_hashes={
            name: value["manifest_hash"] for name, value in candidates.items()
        },
        provenance=comparison["provenance"],
        final_test_accessed=False,
    )
    return selection | {"selection_hash": content_hash(selection)}, comparison


def record_selection(cfg):
    reports = [
        json.loads((ROOT / f"docs/exports/{name}_tuning.json").read_text())
        for name in ("logistic", "random_forest", "xgboost")
    ]
    selection, comparison = select_candidate(
        reports, cfg["training"]["selection"]["minimum_complex_model_ap_gain"]
    )
    paths = {"logistic": "logistic", "random_forest": "random_forest", "xgboost": "xgb_failure"}
    for name, path in paths.items():
        manifest, _ = load_model(ROOT / "artifacts" / path)
        if manifest["manifest_hash"] != selection["candidate_manifest_hashes"][name]:
            raise ValueError("Reference report and model artifact mismatch")
    write_json(ROOT / "artifacts/failure_selection.json", selection)
    write_json(ROOT / "docs/exports/model_baselines_comparison.json", comparison)
    return selection
