"""One-shot frozen failure evaluation; official outcomes never select a model."""

import json
import os
from dataclasses import replace

import numpy as np
import pandas as pd
from fleetiq_data.contracts import NASA_COLUMNS
from fleetiq_features.__main__ import benchmark_inputs
from fleetiq_features.pipeline import PipelineFit, snapshot
from fleetiq_training.calibration import probabilities, reliability
from fleetiq_training.classification import (
    engine_bootstrap,
    file_hash,
    load_model,
    metrics,
    write_json,
)

from fleetiq_evaluation.splits import ROOT, content_hash, qualified_engine


def once(receipt, key, compute):
    receipt.parent.mkdir(parents=True, exist_ok=True)
    try:
        with receipt.open("x") as handle:
            json.dump(dict(state="pending", key=key), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        saved = json.loads(receipt.read_text())
        if saved["key"] != key:
            raise ValueError(
                "Final outcomes already consumed by a different frozen bundle/config; obtain untouched test data"
            )
        if saved["state"] != "complete":
            raise ValueError(
                "Incomplete final evaluation receipt needs review; outcomes cannot be silently reread"
            )
        if (
            content_hash(
                {field: value for field, value in saved["report"].items() if field != "report_hash"}
            )
            != saved["report"]["report_hash"]
        ):
            raise ValueError("Final cached evidence integrity mismatch")
        return saved["report"]
    report = compute()
    report["report_hash"] = content_hash(report)
    temporary = receipt.with_suffix(".pending.json")
    write_json(temporary, dict(state="complete", key=key, report=report))
    os.replace(temporary, receipt)
    return report


def final_labels(observed, rul):
    if (
        set(observed.columns) != set(NASA_COLUMNS)
        or observed[["unit_id", "cycle"]].duplicated().any()
    ):
        raise ValueError("Unique observed NASA test keys required")
    keys = observed[["unit_id", "cycle"]]
    if not np.isfinite(keys.to_numpy()).all() or ((keys < 1) | (keys % 1 != 0)).any().any():
        raise ValueError("Positive native test keys required")
    units = sorted(int(value) for value in observed.unit_id.unique())
    rul = np.asarray(rul, dtype=float).reshape(-1)
    if (
        units != list(range(1, len(units) + 1))
        or len(rul) != len(units)
        or not np.isfinite(rul).all()
        or np.any((rul < 0) | (rul % 1 != 0))
    ):
        raise ValueError("Official RUL endpoint mapping is invalid")
    endpoints = observed.groupby("unit_id", sort=True).cycle.max().reset_index()
    endpoints["stream"] = [qualified_engine(unit, split="test") for unit in units]
    endpoints["rul_cycles"] = rul.astype(int)
    endpoints["event_cycle"] = endpoints.cycle + endpoints.rul_cycles
    endpoints["eligible"] = endpoints.rul_cycles > 0
    endpoints["failure_within_horizon"] = (endpoints.rul_cycles > 0) & (endpoints.rul_cycles <= 30)
    return endpoints


def validate_bundle(folder, cfg):
    bundle = json.loads((folder / "calibration.json").read_text())
    if (
        content_hash({key: value for key, value in bundle.items() if key != "bundle_hash"})
        != bundle["bundle_hash"]
    ):
        raise ValueError("Frozen calibration bundle integrity mismatch")
    manifest, predict = load_model(folder)
    fit = PipelineFit.load(folder / "preprocessing.json")
    if (
        manifest["manifest_hash"] != bundle["model_manifest_hash"]
        or fit.content_hash != manifest["provenance"]["preprocessing_hash"]
    ):
        raise ValueError("Model/feature/calibration binding mismatch")
    if (
        bundle["experiment_hash"] != content_hash(cfg)
        or bundle["evaluation_protocol"] != cfg["evaluation"]
    ):
        raise ValueError("Experiment/anchors changed after freezing")
    if (
        bundle["threshold"] != manifest["threshold"]
        or bundle["threshold_scale"] != "uncalibrated_score"
    ):
        raise ValueError("Threshold changed after tuning")
    if bundle["track"] != "cmapss_benchmark" or bundle["horizon_cycles"] != 30:
        raise ValueError("Only the declared native-cycle benchmark failure task is supported")
    return bundle, manifest, predict, fit


def evaluate(folder, cfg, *, root=ROOT):
    bundle, manifest, predict, fit = validate_bundle(folder, cfg)
    key = content_hash(
        dict(
            bundle_hash=bundle["bundle_hash"],
            experiment_hash=content_hash(cfg),
            model_hash=manifest["model_hash"],
            schema_hash=fit.schema_hash,
        )
    )

    def compute():
        observed_path = root / "data/processed/cmapss/test_observed.parquet"
        target_path = root / "data/raw/cmapss/RUL_FD001.txt"
        for path in (observed_path, target_path):
            if path.resolve() != path.absolute():
                raise ValueError("Official test inputs cannot redirect through symlinks")
        # First access to these outcomes occurs only after the exclusive receipt is written.
        observed = pd.read_parquet(observed_path, columns=list(NASA_COLUMNS))
        endpoints = final_labels(observed, np.loadtxt(target_path, ndmin=1))
        last = dict(zip(endpoints.unit_id, endpoints.cycle, strict=True))
        vectors, identities, input_hashes, unsupported = [], [], [], []
        for original in benchmark_inputs(observed):
            unit = int(original.group)
            if original.as_of != last[unit]:
                continue
            stream = qualified_engine(unit, split="test")
            spec = replace(
                original,
                stream=stream,
                channels={
                    name: tuple(replace(row, stream=stream) for row in rows)
                    for name, rows in original.channels.items()
                },
                settings={
                    name: tuple(replace(row, stream=stream) for row in rows)
                    for name, rows in original.settings.items()
                },
            )
            value = snapshot(spec, fit)
            if not value["supported"]:
                unsupported.append(stream)
                continue
            identities.append(unit)
            vectors.append(value["vector"])
            input_hashes.append(value["input_hash"])
        rows = endpoints.loc[endpoints.unit_id.isin(identities) & endpoints.eligible].sort_values(
            "unit_id"
        )
        by_unit = dict(zip(identities, vectors, strict=True))
        if rows.empty:
            raise ValueError("No supported eligible final endpoint observations")
        scores = predict(np.array([by_unit[unit] for unit in rows.unit_id]))
        calibrated = probabilities(scores, bundle["calibration"])
        calibrated_metrics = (
            reliability(rows, calibrated["probability"]) if calibrated["supported"] else None
        )
        result = metrics(rows, scores, bundle["threshold"], exposure=0)
        result["false_alerts_per_1000_endpoint_reviews"] = result["false_alerts"] / len(rows) * 1000
        result["exposure_cycles"] = None
        result["cycle_rate_unavailable_reason"] = (
            "one_endpoint_review_per_engine_no_continuous_alert_exposure"
        )
        bootstrap = engine_bootstrap(
            rows,
            scores,
            bundle["threshold"],
            seed=cfg["training"]["seed"],
            repetitions=cfg["evaluation"]["bootstrap_engines"],
            endpoint_only=True,
        )
        return dict(
            version="failure-final-evaluation-v1",
            role="frozen_official_test_once",
            track="cmapss_benchmark",
            life_unit="cycles",
            horizon_cycles=30,
            anchor="official_last_observed_cycle_only",
            model=manifest["model"],
            metrics=result,
            bootstrap=bootstrap,
            probability_display_enabled=False,
            calibration_supported=bundle["calibration"]["supported"],
            calibrated_brier=calibrated_metrics["brier"] if calibrated_metrics else None,
            calibrated_log_loss=calibrated_metrics["log_loss"] if calibrated_metrics else None,
            native_observed_engines=len(endpoints),
            eligible_supported_engines=len(rows),
            unsupported_engines=unsupported,
            already_failed_engines=int((~endpoints.eligible).sum()),
            frozen=dict(
                bundle_hash=bundle["bundle_hash"],
                selection_hash=bundle["selection_hash"],
                model_manifest_hash=manifest["manifest_hash"],
                model_hash=manifest["model_hash"],
                schema_hash=fit.schema_hash,
                preprocessing_hash=fit.content_hash,
                split_hash=manifest["provenance"]["split_hash"],
                threshold=bundle["threshold"],
                observed_hash=file_hash(observed_path),
                target_hash=file_hash(target_path),
                final_feature_input_hash=content_hash(input_hashes),
            ),
            final_outcomes_used_for_selection=False,
            limitations=[
                "uncalibrated_scores_not_operational_probabilities",
                "endpoint_sampling_differs_from_tuning_grid",
                "warning_lead_is_remaining_native_cycles_at_endpoint_not_continuous_detection_delay",
                "simulated_public_engines_not_aircraft_certification",
            ],
        )

    return once(root / "docs/exports/failure_final_once.json", key, compute)
