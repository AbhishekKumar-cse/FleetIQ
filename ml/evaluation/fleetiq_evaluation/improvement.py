"""Assess a frozen improved bundle once on the untouched FD003 endpoints."""

import argparse
import json
from dataclasses import replace
from zipfile import ZipFile

import numpy as np
import yaml
from fleetiq_data.cmapss import parse_evaluator_rul, parse_trajectory
from fleetiq_features.__main__ import benchmark_inputs
from fleetiq_features.pipeline import PipelineFit, snapshot
from fleetiq_training.calibration import fit_sigmoid, probabilities, reliability
from fleetiq_training.classification import file_hash, load_model, write_json
from fleetiq_training.degradation_features import build_table, development
from fleetiq_training.improvement import blend, load_component, scores_metrics

from fleetiq_evaluation.classification import once
from fleetiq_evaluation.splits import ROOT, content_hash


def validate_selection(folder, cfg):
    selection = json.loads((folder / "selection.json").read_text())
    if (
        selection["selection_hash"]
        != content_hash({k: v for k, v in selection.items() if k != "selection_hash"})
        or selection["experiment_hash"] != content_hash(cfg)
        or selection["final_test_accessed"]
    ):
        raise ValueError("Frozen selection/config integrity mismatch")
    sets = [set(selection["groups"][role]) for role in ("fit", "tune", "calibration")]
    if any(sets[a] & sets[b] for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Engine roles overlap")
    for name in selection["candidate"]["components"] + ["baseline_xgboost"]:
        record = json.loads((folder / name / "component.json").read_text())
        if record != selection["candidates"][name]["artifact"]:
            raise ValueError("Native component changed after selection")
        load_component(folder / name, cfg["threads"])
    return selection


def predict_table(folder, table, candidate, threads):
    values = {}
    for name in candidate["components"]:
        names, predict = load_component(folder / name, threads)
        values[name] = predict(table[names].to_numpy())
    return blend(values, candidate)


def calibrate(root, folder, cfg):
    selection = validate_selection(folder, cfg)
    target = folder / "calibration.json"
    if target.exists():
        body = json.loads(target.read_text())
        if body["selection_hash"] != selection["selection_hash"]:
            raise ValueError("Calibration selection changed")
        return body
    frame, hashes = development(root, cfg)
    if hashes != selection["source_hashes"]:
        raise ValueError("Training source changed after selection")
    frame = frame.loc[frame.role == "calibration"]
    enhanced = selection["chosen"] != "baseline_xgboost"
    rows = build_table(frame, cfg, enhanced=enhanced)
    rows = rows.loc[(rows.cycle - cfg["minimum_cycle"]) % 10 == 0]
    scores = predict_table(folder, rows, selection["candidate"], cfg["threads"])
    calibration = fit_sigmoid(
        scores,
        rows,
        fit_engines=selection["groups"]["fit"],
        tune_engines=selection["groups"]["tune"],
        calibration_engines=selection["groups"]["calibration"],
        minimum_events=max(
            cfg["calibration_minimum_positive_engines"],
            cfg["calibration_minimum_negative_engines"],
        ),
        seed=cfg["seed"],
    )
    body = dict(
        selection_hash=selection["selection_hash"],
        calibration=calibration,
        raw_diagnostics=reliability(rows, scores),
        probability_display_enabled=False,
        limitations=["natural_training_grid_prevalence_may_differ_from_endpoint_reviews"],
    )
    body["bundle_hash"] = content_hash(body)
    write_json(target, body)
    write_json(root / "docs/exports/failure_improvement_calibration.json", body)
    return body


def independent_intervals(y, scores, threshold, *, seed, repetitions):
    rng = np.random.default_rng(seed)
    records = []
    for _ in range(repetitions):
        indices = rng.choice(len(y), len(y), replace=True)
        records.append(
            scores_metrics(np.asarray(y)[indices], np.asarray(scores)[indices], threshold)
        )
    intervals = {}
    for metric in ("precision", "recall", "f1", "accuracy", "average_precision"):
        values = [r[metric] for r in records if r[metric] is not None]
        intervals[metric] = np.quantile(values, [0.025, 0.975]).tolist() if values else None
    return dict(
        unit="whole_engine_endpoint",
        confidence_level=0.95,
        repetitions=repetitions,
        intervals=intervals,
    )


def endpoint_rows(observed, rul, cfg):
    ids = sorted(observed.unit_id.unique().tolist())
    targets = parse_evaluator_rul(rul, ids)
    endpoints = (
        observed.groupby("unit_id", sort=True)
        .cycle.max()
        .reset_index()
        .merge(targets, on="unit_id", validate="one_to_one")
    )
    endpoints["stream"] = [f"NASA:FD003:test:{u}" for u in endpoints.unit_id]
    endpoints["event_cycle"] = endpoints.cycle + endpoints.rul_cycles
    endpoints["failure_within_horizon"] = (
        (endpoints.rul_cycles > 0) & (endpoints.rul_cycles <= cfg["horizon_cycles"])
    ).astype(int)
    return endpoints


def legacy_scores(observed, folder):
    """Unchanged Step 55 model on the same fresh endpoint cohort, as a reference."""
    manifest, predict = load_model(folder)
    fit = PipelineFit.load(folder / "preprocessing.json")
    last = observed.groupby("unit_id").cycle.max().to_dict()
    vectors = []
    for spec in benchmark_inputs(observed):
        if spec.as_of != last[int(spec.group)]:
            continue
        stream = f"NASA:FD003:test:{spec.group}"
        spec = replace(
            spec,
            stream=stream,
            channels={
                name: tuple(replace(row, stream=stream) for row in rows)
                for name, rows in spec.channels.items()
            },
            settings={
                name: tuple(replace(row, stream=stream) for row in rows)
                for name, rows in spec.settings.items()
            },
        )
        value = snapshot(spec, fit)
        if not value["supported"]:
            raise ValueError("Legacy endpoint coverage unsupported")
        vectors.append(value["vector"])
    return manifest, predict(np.asarray(vectors))


def evaluate(root, folder, cfg):
    if cfg["final_subset"] != "FD003" or cfg["horizon_cycles"] != 30:
        raise ValueError("Only predeclared untouched FD003 endpoint test supported")
    selection = validate_selection(folder, cfg)
    calibration = json.loads((folder / "calibration.json").read_text())
    if (
        calibration["bundle_hash"]
        != content_hash({k: v for k, v in calibration.items() if k != "bundle_hash"})
        or calibration["selection_hash"] != selection["selection_hash"]
    ):
        raise ValueError("Calibration bundle changed")
    legacy, _ = load_model(root / "artifacts/xgb_failure")
    key = content_hash(
        dict(
            selection=selection["selection_hash"],
            calibration=calibration["bundle_hash"],
            legacy=legacy["manifest_hash"],
            evaluator=file_hash(ROOT / "ml/evaluation/fleetiq_evaluation/improvement.py"),
        )
    )

    def compute():
        archive = root / "data/raw/cmapss/CMAPSSData.zip"
        if file_hash(archive) != "74bef434a34db25c7bf72e668ea4cd52afe5f2cf8e44367c55a82bfd91a5a34f":
            raise ValueError("Official source archive changed")
        with ZipFile(archive) as bundle:
            for name in ("test_FD003.txt", "RUL_FD003.txt"):
                path = root / "data/raw/cmapss" / name
                if path.exists() and path.read_bytes() != bundle.read(name):
                    raise ValueError("Official test source changed")
                if not path.exists():
                    path.write_bytes(bundle.read(name))
        observed = parse_trajectory(root / "data/raw/cmapss/test_FD003.txt")
        labels = endpoint_rows(observed, root / "data/raw/cmapss/RUL_FD003.txt", cfg)
        frame = observed.copy()
        frame["stream"] = [f"NASA:FD003:test:{u}" for u in frame.unit_id]
        frame["role"] = "final"
        enhanced = selection["chosen"] != "baseline_xgboost"
        table = build_table(frame, cfg, enhanced=enhanced, outcomes=False)
        table = table.sort_values("cycle").groupby("stream", sort=True).tail(1)
        rows = labels.merge(table, on=["stream", "cycle"], validate="one_to_one").sort_values(
            "unit_id"
        )
        excluded = rows.loc[rows.rul_cycles <= 0, "stream"].tolist()
        rows = rows.loc[rows.rul_cycles > 0]
        values = predict_table(folder, rows, selection["candidate"], cfg["threads"])
        threshold = selection["candidate"]["tune"]["threshold"]
        assessed = scores_metrics(rows.failure_within_horizon, values, threshold)
        _, old_values = legacy_scores(observed, root / "artifacts/xgb_failure")
        old_values = old_values[labels.rul_cycles.to_numpy() > 0]
        old_result = scores_metrics(rows.failure_within_horizon, old_values, legacy["threshold"])
        basic = build_table(frame, cfg, enhanced=False, outcomes=False)
        basic = basic.sort_values("cycle").groupby("stream", sort=True).tail(1)
        basic_rows = labels.merge(basic, on=["stream", "cycle"], validate="one_to_one").sort_values(
            "unit_id"
        )
        basic_rows = basic_rows.loc[basic_rows.rul_cycles > 0]
        baseline = selection["candidates"]["baseline_xgboost"]
        basic_values = predict_table(folder, basic_rows, baseline, cfg["threads"])
        calibrated = probabilities(values, calibration["calibration"])
        return dict(
            version="failure-improvement-final-v1",
            model=selection["chosen"],
            subset="FD003",
            horizon_cycles=30,
            role="untouched_official_test_once",
            selection_hash=selection["selection_hash"],
            bundle_hash=calibration["bundle_hash"],
            final_outcomes_used_for_selection=False,
            excluded_already_failed=excluded,
            metrics=assessed,
            bootstrap=independent_intervals(
                rows.failure_within_horizon.to_numpy(),
                values,
                threshold,
                seed=cfg["seed"],
                repetitions=cfg["final_repetitions"],
            ),
            compact_features_same_training_reference=scores_metrics(
                rows.failure_within_horizon, basic_values, baseline["tune"]["threshold"]
            ),
            unchanged_step055_reference=old_result,
            calibrated_diagnostics=reliability(rows, calibrated["probability"])
            if calibrated["supported"]
            else None,
            calibration_supported=calibrated["supported"],
            probability_display_enabled=False,
            aspiration_met=all(
                assessed[k] is not None and assessed[k] >= v for k, v in cfg["aspiration"].items()
            ),
            test_source_hashes={
                name: file_hash(root / "data/raw/cmapss" / name)
                for name in ("test_FD003.txt", "RUL_FD003.txt")
            },
            limitations=[
                "public_simulated_engines_not_aircraft_validation",
                "FD003_additional_fault_mode_differs_from_FD001",
                "legacy_reference_has_less_training_data_and_no_FD003_fit_engines",
                "no_threshold_or_model_changes_after_this_test",
                "probability_grid_prevalence_differs_from_endpoint_sampling",
            ],
        )

    return once(root / "docs/exports/failure_improvement_final_once.json", key, compute)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["calibrate", "evaluate"], required=True)
    args = parser.parse_args()
    cfg = yaml.safe_load((ROOT / "config/failure_improvement.yaml").read_text())
    folder = ROOT / "artifacts/failure_improvement_v1"
    if args.stage == "calibrate":
        report = calibrate(ROOT, folder, cfg)
        print(report["calibration"]["counts"], report["calibration"]["supported"])
    else:
        report = evaluate(ROOT, folder, cfg)
        write_json(ROOT / "docs/exports/failure_improvement_final.json", report)
        print(report["model"], report["metrics"], "aspiration_met", report["aspiration_met"])


if __name__ == "__main__":
    main()
