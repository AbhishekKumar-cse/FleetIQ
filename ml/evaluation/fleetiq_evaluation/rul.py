"""Frozen endpoint RUL evaluation; repeats reuse an integrity-checked receipt."""

import json
from dataclasses import replace

import numpy as np
import pandas as pd
from fleetiq_data.contracts import NASA_COLUMNS
from fleetiq_features.__main__ import benchmark_inputs
from fleetiq_features.pipeline import PipelineFit, snapshot
from fleetiq_training.classification import file_hash
from fleetiq_training.rul_baseline import load_baseline, predict_rul, regression_metrics

from fleetiq_evaluation.classification import final_labels, once
from fleetiq_evaluation.splits import ROOT, content_hash, qualified_engine


def nasa_score(prediction, truth):
    error = np.asarray(prediction, dtype=float) - np.asarray(truth, dtype=float)
    if not len(error) or not np.isfinite(error).all():
        raise ValueError("Finite nonempty aligned errors required")
    exponent = np.where(error >= 0, error / 10, -error / 13)
    maximum = float(exponent.max())
    log_sum_exp = maximum + float(np.log(np.exp(exponent - maximum).sum()))
    # Report overflow explicitly instead of clipping errors to improve the score.
    total = float(np.expm1(exponent).sum()) if log_sum_exp < 709 else None
    return dict(
        total=total,
        overflow=total is None,
        log_sum_exp=log_sum_exp,
        optimistic_scale_cycles=10,
        pessimistic_scale_cycles=13,
    )


def endpoint_vector(spec, fit):
    stream = qualified_engine(int(spec.group), split="test")
    return snapshot(
        replace(
            spec,
            stream=stream,
            channels={
                k: tuple(replace(r, stream=stream) for r in rows)
                for k, rows in spec.channels.items()
            },
            settings={
                k: tuple(replace(r, stream=stream) for r in rows)
                for k, rows in spec.settings.items()
            },
        ),
        fit,
    )


def evaluate_rul(folder, cfg, *, root=ROOT):
    calibration = json.loads((folder / "intervals.json").read_text())
    if (
        content_hash({k: v for k, v in calibration.items() if k != "calibration_hash"})
        != calibration["calibration_hash"]
    ):
        raise ValueError("RUL calibration integrity mismatch")
    model_folder = (root / calibration["model_folder"]).resolve()
    if not model_folder.is_relative_to((root / "artifacts").resolve()):
        raise ValueError("Untrusted model path")
    model = load_baseline(model_folder)
    fit_path = root / "data/processed/features/preprocessing.json"
    fit = PipelineFit.load(fit_path)
    if (
        model["bundle_hash"] != calibration["model_bundle_hash"]
        or fit.content_hash != model["preprocessing"]["hash"]
        or cfg["rul_training"] != calibration["provenance"]["rul_contract"]
    ):
        raise ValueError("Frozen RUL model/preprocessing/contract changed")
    key = content_hash(
        dict(
            calibration_hash=calibration["calibration_hash"],
            model_hash=model["bundle_hash"],
            preprocessing_file_hash=file_hash(fit_path),
        )
    )

    def compute():
        observed_path = root / "data/processed/cmapss/test_observed.parquet"
        targets_path = root / "data/raw/cmapss/RUL_FD001.txt"
        if any(p.resolve() != p.absolute() for p in (observed_path, targets_path)):
            raise ValueError("Redirected official test input")
        observed = pd.read_parquet(observed_path, columns=list(NASA_COLUMNS))
        endpoints = final_labels(observed, np.loadtxt(targets_path, ndmin=1))
        endpoints["life_unit"] = "cycles"
        last = dict(zip(endpoints.unit_id, endpoints.cycle, strict=True))
        values, excluded = {}, []
        for spec in benchmark_inputs(observed):
            unit = int(spec.group)
            if spec.as_of != last[unit]:
                continue
            if spec.as_of < cfg["rul_training"]["minimum_cycle"]:
                excluded.append(dict(unit_id=unit, reason="short_history_before_cycle_30"))
                continue
            result = endpoint_vector(spec, fit)
            if not result["supported"]:
                excluded.append(dict(unit_id=unit, reason="unsupported_feature_coverage"))
            else:
                values[unit] = result["vector"]
        rows = endpoints.loc[endpoints.eligible & endpoints.unit_id.isin(values)].copy()
        if rows.empty:
            raise ValueError("No eligible RUL endpoints")
        predictions = predict_rul(
            model_folder, np.asarray([values[u] for u in rows.unit_id]), rows.cycle
        )
        result = regression_metrics(rows, predictions)
        result["nasa_score"] = nasa_score(predictions, rows.rul_cycles)
        interval = calibration["calibration"]
        result["intervals"] = dict(
            supported=interval["supported"],
            reasons=interval["reasons"],
            nominal_coverage=interval["nominal_coverage"],
            coverage=None,
            mean_width_cycles=None,
            evaluated_count=0,
            display_enabled=False,
        )
        if interval["supported"]:
            radius = interval["displayed_radius_cycles"]
            point = np.maximum(0, predictions)
            lower, upper = np.maximum(0, point - radius), point + radius
            result["intervals"].update(
                coverage=float(np.mean((rows.rul_cycles >= lower) & (rows.rul_cycles <= upper))),
                mean_width_cycles=float(np.mean(upper - lower)),
                evaluated_count=len(rows),
            )
        return dict(
            metrics=result,
            total_engines=len(endpoints),
            evaluated_engines=len(rows),
            excluded=excluded,
            terminal_failed_engines=int((~endpoints.eligible).sum()),
            endpoint_predictions=[
                dict(unit_id=int(u), truth_cycles=int(t), prediction_cycles=float(p))
                for u, t, p in zip(rows.unit_id, rows.rul_cycles, predictions, strict=True)
            ],
            model_bundle_hash=model["bundle_hash"],
            calibration_hash=calibration["calibration_hash"],
            inputs={"observations": file_hash(observed_path), "targets": file_hash(targets_path)},
            life_unit="cycles",
            target_cap=None,
            selection_changed=False,
            prior_test_inspection="FD001 outcomes previously inspected for failure classification; this is frozen RUL evaluation, not untouched data",
            lead_time=dict(
                supported=False,
                reason="endpoint RUL alone cannot measure observed maintenance-warning lead time",
            ),
        )

    return once(folder / "final_rul_receipt.json", key, compute)
