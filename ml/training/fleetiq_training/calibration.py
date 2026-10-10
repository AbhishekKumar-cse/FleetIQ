"""Disjoint natural-prevalence sigmoid calibration, or explicit abstention."""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import yaml
from fleetiq_evaluation.splits import ROOT, content_hash
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

from fleetiq_training.classification import (
    experiment,
    load_development,
    load_model,
    metrics,
    sigmoid,
    write_json,
)


def fit_sigmoid(
    scores,
    rows,
    *,
    fit_engines,
    tune_engines,
    calibration_engines,
    minimum_events,
    seed,
    anchor_column="cycle",
):
    fit_set, tune_set, cal_set = set(fit_engines), set(tune_engines), set(calibration_engines)
    if fit_set & tune_set or fit_set & cal_set or tune_set & cal_set or set(rows.stream) - cal_set:
        raise ValueError("Calibration overlaps fit/tune or contains undeclared engines")
    scores = np.asarray(scores, dtype=float)
    if (
        len(scores) != len(rows)
        or not np.isfinite(scores).all()
        or np.any((scores < 0) | (scores > 1))
    ):
        raise ValueError("Finite bounded calibration scores required")
    y = rows.failure_within_horizon.astype(int).to_numpy()
    positive = rows.loc[y == 1]
    negative = rows.loc[y == 0]
    counts = dict(
        samples=len(rows),
        positive_windows=int(y.sum()),
        negative_windows=int(len(y) - y.sum()),
        independent_positive_events=positive.stream.nunique(),
        positive_engines=positive.stream.nunique(),
        negative_engines=negative.stream.nunique(),
        independent_engines=rows.stream.nunique(),
    )
    provenance = dict(
        calibration_engines=sorted(cal_set),
        engine_hash=content_hash(sorted(cal_set)),
        label_hash=content_hash(
            rows[["stream", anchor_column, "failure_within_horizon"]].to_dict("records")
        ),
        score_hash=content_hash(scores.tolist()),
    )
    if (
        min(
            counts["positive_engines"],
            counts["negative_engines"],
            counts["independent_positive_events"],
        )
        < minimum_events
    ):
        return dict(
            supported=False,
            reason="insufficient_independent_calibration_events",
            counts=counts,
            minimum_independent_events=minimum_events,
            provenance=provenance,
            parameters=None,
        )
    margin = np.log(np.clip(scores, 1e-12, 1 - 1e-12) / (1 - np.clip(scores, 1e-12, 1 - 1e-12)))
    model = LogisticRegression(C=1.0, solver="lbfgs", random_state=seed, max_iter=500).fit(
        margin[:, None], y
    )
    parameters = dict(slope=float(model.coef_[0, 0]), intercept=float(model.intercept_[0]))
    return dict(
        supported=True,
        reason="sigmoid_fit_natural_prevalence",
        counts=counts,
        provenance=provenance,
        parameters=parameters,
        calibration_hash=content_hash(parameters),
        deployment_approved=False,
    )


def probabilities(scores, calibration):
    if not calibration["supported"]:
        return dict(probability=None, supported=False, reason=calibration["reason"])
    scores = np.asarray(scores, dtype=float)
    if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
        raise ValueError("Bounded finite scores required for calibrated probabilities")
    margin = np.log(np.clip(scores, 1e-12, 1 - 1e-12) / (1 - np.clip(scores, 1e-12, 1 - 1e-12)))
    params = calibration["parameters"]
    return dict(probability=sigmoid(params["slope"] * margin + params["intercept"]), supported=True)


def reliability(rows, scores):
    y = rows.failure_within_horizon.astype(int).to_numpy()
    bins = []
    for index in range(10):
        selected = (scores >= index / 10) & (
            scores < (index + 1) / 10 if index < 9 else scores <= 1
        )
        bins.append(
            dict(
                lower=index / 10,
                upper=(index + 1) / 10,
                count=int(selected.sum()),
                mean_score=float(scores[selected].mean()) if selected.any() else None,
                observed_fraction=float(y[selected].mean()) if selected.any() else None,
            )
        )
    return dict(
        bins=bins,
        brier=float(brier_score_loss(y, scores)),
        log_loss=float(log_loss(y, scores, labels=[0, 1])),
    )


def sensitivity(rows, scores, policy):
    results = []
    candidate_metrics = [
        metrics(rows, scores, value) for value in np.unique(np.r_[scores, np.nextafter(1.0, 2.0)])
    ]
    for costs in policy["cost_sensitivity"]:
        chosen = min(
            candidate_metrics,
            key=lambda item: (
                costs["missed_event"]
                * (item["independent_positive_events"] - item["detected_events"])
                + costs["false_review"] * item["false_alerts"],
                -item["threshold"],
            ),
        )
        results.append(
            dict(
                costs=costs,
                threshold=chosen["threshold"],
                false_reviews=chosen["false_alerts"],
                missed_events=chosen["independent_positive_events"] - chosen["detected_events"],
                role="tune_only_sensitivity_not_active_policy",
            )
        )
    return results


def calibrate(requested, cfg, output):
    selection_path = requested.parent / "failure_selection.json"
    selection = json.loads(selection_path.read_text())
    if (
        content_hash({key: value for key, value in selection.items() if key != "selection_hash"})
        != selection["selection_hash"]
        or selection["role"] != "tune_only"
    ):
        raise ValueError("Frozen tuning selection integrity required")
    paths = dict(logistic="logistic", random_forest="random_forest", xgboost="xgb_failure")
    source = requested.parent / paths[selection["chosen"]]
    manifest, predict = load_model(source)
    if cfg["evaluation"] != manifest["evaluation_protocol"]:
        raise ValueError("Decision anchors/threshold criteria changed after selection")
    if manifest["manifest_hash"] != selection["candidate_manifest_hashes"][selection["chosen"]]:
        raise ValueError("Selected model changed before calibration")
    data, fit, provenance = load_development(cfg, roles=("fit", "tune", "calibration"))
    for role in ("fit", "tune"):
        for field in ("anchors", "vectors", "labels"):
            if provenance[field][role] != manifest["provenance"][field][role]:
                raise ValueError("Selection/calibration feature, label or anchor mismatch")
    for field in ("split_hash", "input_hash", "preprocessing_hash", "schema_hash"):
        if provenance[field] != manifest["provenance"][field]:
            raise ValueError("Selection/calibration provenance mismatch")
    scores = predict(data["calibration"][list(fit.names)].to_numpy())
    requirements = cfg["tasks"]["failure_risk"]
    minimum = max(
        requirements["minimum_calibration_events"],
        requirements["minimum_calibration_positive_engines"],
        requirements["minimum_calibration_negative_engines"],
    )
    if minimum < 20:
        raise ValueError("The frozen independent-event floor cannot be reduced after selection")
    calibration = fit_sigmoid(
        scores,
        data["calibration"],
        fit_engines=provenance["groups"]["fit"],
        tune_engines=provenance["groups"]["tune"],
        calibration_engines=provenance["groups"]["calibration"],
        minimum_events=minimum,
        seed=cfg["training"]["seed"],
    )
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("model.json", "manifest.json", "preprocessing.json"):
        shutil.copyfile(source / filename, output / filename)
    bundle = dict(
        version="failure-calibration-bundle-v1",
        model_manifest_hash=manifest["manifest_hash"],
        selection_hash=selection["selection_hash"],
        selected_model=manifest["model"],
        threshold=selection["threshold"],
        threshold_scale="uncalibrated_score",
        calibration=calibration,
        calibration_provenance=provenance,
        track=manifest["track"],
        horizon_cycles=30,
        probability_display_enabled=False,
        final_test_accessed=False,
        experiment_hash=content_hash(cfg),
        evaluation_protocol=cfg["evaluation"],
    )
    bundle["bundle_hash"] = content_hash(bundle)
    write_json(output / "calibration.json", bundle)
    raw = reliability(data["calibration"], scores)
    calibrated = probabilities(scores, calibration)
    assessed = (
        reliability(data["calibration"], calibrated["probability"])
        if calibrated["supported"]
        else None
    )
    policy = yaml.safe_load((ROOT / "config/demo_policy.yaml").read_text())
    tune_scores = predict(data["tune"][list(fit.names)].to_numpy())
    report = dict(
        bundle_hash=bundle["bundle_hash"],
        calibration=calibration,
        raw_score_diagnostics=raw,
        calibrated_diagnostics=assessed,
        cost_sensitivity=sensitivity(data["tune"], tune_scores, policy),
        precise_probability_enabled=False,
        final_test_accessed=False,
        assessment_role="calibration_fit_in_sample_diagnostic_not_final_validation",
    )
    write_json(ROOT / "docs/exports/failure_calibration.json", report)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = [row for row in raw["bins"] if row["count"]]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot([0, 1], [0, 1], "--", color="gray")
    ax.plot(
        [row["mean_score"] for row in points], [row["observed_fraction"] for row in points], "o-"
    )
    ax.set(
        xlabel="Uncalibrated candidate score",
        ylabel="Observed fraction",
        title="Calibration cohort: score reliability (unsupported calibration)",
    )
    fig.tight_layout()
    fig.savefig(ROOT / "docs/exports/failure_reliability.png", dpi=130)
    plt.close(fig)
    return bundle, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        not args.output.resolve().is_relative_to((ROOT / "artifacts").resolve())
        or args.model.resolve() == args.output.resolve()
    ):
        raise ValueError("Distinct controlled artifact output required")
    bundle, _ = calibrate(args.model, experiment(args.config), args.output)
    print(
        bundle["bundle_hash"], bundle["calibration"]["supported"], bundle["calibration"]["reason"]
    )


if __name__ == "__main__":
    main()
