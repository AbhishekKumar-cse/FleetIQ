"""Fair bounded CPU RUL search; tune-only selection can retain a simpler reference."""

import numpy as np
from fleetiq_evaluation.splits import ROOT, content_hash
from threadpoolctl import threadpool_limits
from xgboost import XGBRegressor

from fleetiq_training.classification import file_hash, write_json
from fleetiq_training.rul_baseline import (
    engine_weights,
    load_baseline,
    load_rul,
    regression_metrics,
)


def settings_grid(cfg):
    settings = cfg["rul_training"]["xgboost"]
    depths, iterations = settings["depths"], settings["iterations"]
    if (
        not depths
        or not iterations
        or len(depths) * len(iterations) > 6
        or any(d not in (2, 3, 4, 5, 6) for d in depths)
        or any(not 10 <= n <= 500 for n in iterations)
        or not 0.01 <= settings["learning_rate"] <= 0.2
    ):
        raise ValueError("Bounded CPU RUL search required")
    return [
        dict(
            max_depth=d,
            n_estimators=n,
            learning_rate=settings["learning_rate"],
            objective="reg:squarederror",
            tree_method="hist",
            device="cpu",
            n_jobs=cfg["training"]["threads"],
            random_state=cfg["training"]["seed"],
            subsample=1.0,
            colsample_bytree=1.0,
        )
        for d in depths
        for n in iterations
    ]


def fit_candidate(frame, names, settings):
    estimator = XGBRegressor(**settings)
    with threadpool_limits(limits=settings["n_jobs"]):
        estimator.fit(
            frame[names].to_numpy(),
            frame.rul_cycles.to_numpy(),
            sample_weight=engine_weights(frame),
        )
    return estimator


def select_reference(baseline, candidate_metrics, provenance, minimum_gain):
    if baseline["provenance"] != provenance:
        raise ValueError("RUL candidates must share features, targets, engines and anchors")
    if not np.isfinite(minimum_gain) or minimum_gain < 0:
        raise ValueError("Nonnegative frozen improvement margin required")
    reference = baseline["tune"][baseline["chosen"]]
    gain = reference["mae_cycles"] - candidate_metrics["mae_cycles"]
    return dict(
        chosen="xgboost" if gain >= minimum_gain else baseline["chosen"],
        gain_cycles=gain,
        minimum_gain_cycles=minimum_gain,
        reference=reference,
        candidate=candidate_metrics,
        selection_role="tune_only",
        official_test_accessed=False,
    )


def train_xgb_rul(cfg, output):
    output = output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("RUL bundles belong under ignored artifacts")
    baseline_folder = ROOT / "artifacts/rul_ridge"
    baseline = load_baseline(baseline_folder)
    data, preprocessing, provenance = load_rul(cfg)
    if baseline["preprocessing"]["names"] != list(preprocessing.names):
        raise ValueError("RUL feature schema mismatch")
    names = list(preprocessing.names)
    candidates, estimators = [], []
    for settings in settings_grid(cfg):
        estimator = fit_candidate(data["fit"], names, settings)
        result = regression_metrics(data["tune"], estimator.predict(data["tune"][names].to_numpy()))
        candidates.append(dict(settings=settings, metrics=result))
        estimators.append(estimator)
    index = min(range(len(candidates)), key=lambda i: (candidates[i]["metrics"]["mae_cycles"], i))
    selected = select_reference(
        baseline,
        candidates[index]["metrics"],
        provenance,
        cfg["rul_training"]["minimum_complex_model_mae_gain_cycles"],
    )
    output.mkdir(parents=True, exist_ok=True)
    estimators[index].save_model(output / "regressor.json")
    loaded = XGBRegressor(device="cpu", n_jobs=cfg["training"]["threads"])
    loaded.load_model(output / "regressor.json")
    if not np.array_equal(
        loaded.predict(data["tune"][names].to_numpy()),
        estimators[index].predict(data["tune"][names].to_numpy()),
    ):
        raise ValueError("Native RUL save/reload parity failed")
    report = dict(
        kind="xgboost",
        chosen="xgboost",
        life_unit="cycles",
        early_life_cap=None,
        candidates=candidates,
        settings=candidates[index]["settings"],
        tune=candidates[index]["metrics"],
        provenance=provenance,
        preprocessing=baseline["preprocessing"],
        model_file_hash=file_hash(output / "regressor.json"),
        official_test_accessed=False,
        selection=selected,
    )
    report["bundle_hash"] = content_hash(report)
    write_json(output / "model.json", report)
    selected_folder = output if selected["chosen"] == "xgboost" else baseline_folder
    selected_bundle = report if selected["chosen"] == "xgboost" else baseline
    selection = dict(
        selected,
        model_folder=selected_folder.relative_to(ROOT).as_posix(),
        model_bundle_hash=selected_bundle["bundle_hash"],
        provenance=provenance,
        candidate_bundle_hash=report["bundle_hash"],
        reference_bundle_hash=baseline["bundle_hash"],
    )
    selection["selection_hash"] = content_hash(selection)
    write_json(output / "selection.json", selection)
    write_json(ROOT / "docs/exports/rul_xgboost.json", report)
    write_json(ROOT / "docs/exports/rul_selection.json", selection)
    return dict(
        chosen=selection["chosen"],
        tune=selection["candidate"] if selection["chosen"] == "xgboost" else selection["reference"],
    )
