"""Engine-max split-conformal RUL evidence with explicit unsupported states."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from fleetiq_evaluation.splits import ROOT, content_hash

from fleetiq_training.classification import experiment, write_json
from fleetiq_training.rul_baseline import load_baseline, load_rul, predict_rul

SCOPE = "cmapss_benchmark:FD001:native_cycles:uncapped"


def calibrate_engine_intervals(
    frame,
    prediction,
    *,
    fit_engines,
    tune_engines,
    calibration_engines,
    alpha=0.1,
    minimum_engines=20,
):
    fit, tune, calibration = set(fit_engines), set(tune_engines), set(calibration_engines)
    if fit & tune or fit & calibration or tune & calibration or set(frame.stream) != calibration:
        raise ValueError("Exactly disjoint declared calibration engines required")
    if frame.empty or not 0 < alpha < 1 or minimum_engines < 1:
        raise ValueError("Finite calibration sample and valid support settings required")
    if set(frame.life_unit) != {"cycles"} or not (frame.rul_cycles > 0).all():
        raise ValueError("Nonterminal native-cycle calibration required")
    if frame[["stream", "cycle"]].duplicated().any():
        raise ValueError("Duplicate decision anchors cannot inflate calibration")
    values = np.asarray(prediction, dtype=float)
    if values.shape != (len(frame),) or not np.isfinite(values).all():
        raise ValueError("Finite aligned calibration predictions required")
    if not np.isfinite(frame.rul_cycles.to_numpy(dtype=float)).all():
        raise ValueError("Finite calibration targets required")
    # Display point policy is nonnegative, so calibration uses that same estimator.
    residual = np.abs(np.maximum(0, values) - frame.rul_cycles.to_numpy(dtype=float))
    scores = pd.Series(residual, index=frame.stream.to_numpy()).groupby(level=0).max().sort_index()
    n = len(scores)
    rank = math.ceil((n + 1) * (1 - alpha))
    reasons = []
    if rank > n:
        reasons.append("finite_sample_rank_exceeds_independent_engines")
    if n < minimum_engines:
        reasons.append("below_declared_minimum_independent_engines")
    radius = float(np.sort(scores.to_numpy())[rank - 1]) if rank <= n else None
    supported = not reasons
    return dict(
        supported=supported,
        reasons=reasons,
        life_unit="cycles",
        target_cap=None,
        nominal_coverage=1 - alpha,
        alpha=alpha,
        rank=rank,
        independent_engines=n,
        minimum_engines=minimum_engines,
        windows=len(frame),
        aggregation="engine_max_absolute_residual",
        engine_scores={str(k): float(v) for k, v in scores.items()},
        candidate_radius_cycles=radius,
        displayed_radius_cycles=radius if supported else None,
        display_enabled=False,
        applicability=SCOPE,
        point_policy="nonnegative",
        coverage_guaranteed=False,
        assumptions="exchangeable engines within declared scope; fixed causal decision anchors",
        observed_calibration_engine_coverage=float(np.mean(scores <= radius))
        if radius is not None
        else None,
    )


def interval_prediction(calibration, raw_prediction, *, scope=SCOPE, shifted=False):
    if not math.isfinite(raw_prediction):
        raise ValueError("Finite cycle prediction required")
    reasons = list(calibration["reasons"])
    if scope != calibration["applicability"]:
        reasons.append("outside_calibration_scope")
    if shifted:
        reasons.append("distribution_shift_review_required")
    if not calibration["supported"] or reasons or not calibration["display_enabled"]:
        return dict(
            supported=False,
            interval=None,
            life_unit="cycles",
            reasons=reasons or ["interval_display_not_enabled"],
        )
    point = max(0, raw_prediction)
    radius = calibration["displayed_radius_cycles"]
    return dict(
        supported=True,
        interval=[max(0, point - radius), point + radius],
        point_cycles=point,
        life_unit="cycles",
        nominal_coverage=calibration["nominal_coverage"],
        coverage_guaranteed=False,
    )


def calibrate_selected(cfg, selection_folder, output):
    output = output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Calibration artifact belongs under ignored artifacts")
    selection = json.loads((selection_folder / "selection.json").read_text())
    if (
        content_hash({k: v for k, v in selection.items() if k != "selection_hash"})
        != selection["selection_hash"]
    ):
        raise ValueError("Frozen selection integrity mismatch")
    folder = (ROOT / selection["model_folder"]).resolve()
    if not folder.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Selected artifact path outside model root")
    model = load_baseline(folder)
    if model["bundle_hash"] != selection["model_bundle_hash"]:
        raise ValueError("Selected model changed after freeze")
    data, preprocessing, provenance = load_rul(cfg, roles=("calibration",))
    if (
        preprocessing.content_hash != model["preprocessing"]["hash"]
        or provenance["split_hash"] != selection["provenance"]["split_hash"]
        or provenance["input_hash"] != selection["provenance"]["input_hash"]
        or provenance["rul_contract"] != selection["provenance"]["rul_contract"]
    ):
        raise ValueError("Calibration data, preprocessing or contract changed")
    frame = data["calibration"]
    predictions = predict_rul(folder, frame[list(preprocessing.names)].to_numpy(), frame.cycle)
    settings = cfg["rul_training"]["intervals"]
    if settings["aggregation"] != "maximum_absolute_residual_per_engine":
        raise ValueError("Frozen engine-max aggregation required")
    calibration = calibrate_engine_intervals(
        frame,
        predictions,
        fit_engines=selection["provenance"]["groups"]["fit"],
        tune_engines=selection["provenance"]["groups"]["tune"],
        calibration_engines=provenance["groups"]["calibration"],
        alpha=settings["alpha"],
        minimum_engines=cfg["tasks"]["rul"]["minimum_calibration_engines"],
    )
    report = dict(
        model=selection["chosen"],
        selection_hash=selection["selection_hash"],
        model_folder=selection["model_folder"],
        model_bundle_hash=model["bundle_hash"],
        calibration=calibration,
        provenance=provenance,
        official_test_accessed=False,
        next_step="061 independent final RUL evaluation; not performed here",
    )
    report["calibration_hash"] = content_hash(report)
    write_json(output / "intervals.json", report)
    write_json(ROOT / "docs/exports/rul_intervals.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=ROOT / "artifacts/xgb_rul")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/rul_calibrated")
    args = parser.parse_args()
    report = calibrate_selected(
        experiment(ROOT / "config/experiments.yaml"), args.selection, args.output
    )
    print(report["model"], report["calibration"])


if __name__ == "__main__":
    main()
