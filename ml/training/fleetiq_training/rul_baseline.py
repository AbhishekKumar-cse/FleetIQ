"""Uncapped native-cycle RUL references using existing fit-only causal snapshots."""

import json

import numpy as np
from fleetiq_evaluation.splits import ROOT, content_hash
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits

from fleetiq_training.classification import file_hash, load_development, write_json


def validate_contract(cfg):
    spec = cfg["rul_training"]
    if (
        spec["subset"],
        spec["life_unit"],
        spec["target"],
        spec["early_life_cap"],
        spec["minimum_cycle"],
        spec["anchors"],
        spec["engine_weighting"],
    ) != (
        "FD001",
        "cycles",
        "terminal_cycle_minus_observed_cycle",
        None,
        30,
        "every_10_observed_cycles_from_30_excluding_terminal",
        "equal_engine_weight_within_role",
    ):
        raise ValueError("Frozen native-cycle target/cap/anchor contract required")
    return spec


def load_rul(cfg, roles=("fit", "tune"), *, root=ROOT):
    spec = validate_contract(cfg)
    data, preprocessing, provenance = load_development(cfg, root=root, roles=roles)
    for role, frame in data.items():
        part = frame.loc[
            (frame.cycle >= spec["minimum_cycle"])
            & ((frame.cycle - spec["minimum_cycle"]) % 10 == 0)
        ].copy()
        if part.empty or not (part.rul_cycles > 0).all() or set(part.life_unit) != {"cycles"}:
            raise ValueError("Positive nonterminal native-cycle targets required")
        if part.stream.nunique() < cfg["tasks"]["rul"]["minimum_fit_engines"] and role == "fit":
            raise ValueError("Insufficient independent fitting engines")
        data[role] = part
    provenance = dict(
        provenance,
        anchors={
            r: content_hash(p[["stream", "cycle", "input_hash"]].to_dict("records"))
            for r, p in data.items()
        },
        target_hashes={
            r: content_hash(p[["stream", "cycle", "rul_cycles"]].to_dict("records"))
            for r, p in data.items()
        },
        rul_contract=spec,
        life_unit="cycles",
        early_life_cap=None,
    )
    return data, preprocessing, provenance


def engine_weights(frame):
    counts = frame.groupby("stream").stream.transform("size").to_numpy(dtype=float)
    return len(frame) / frame.stream.nunique() / counts


def regression_metrics(frame, prediction):
    prediction = np.asarray(prediction, dtype=float)
    actual = frame.rul_cycles.to_numpy(dtype=float)
    if prediction.shape != actual.shape or not np.isfinite(prediction).all():
        raise ValueError("Finite aligned native-cycle predictions required")
    error = prediction - actual
    weights = engine_weights(frame)
    bins = {}
    for name, lower, upper in (
        ("late_1_30", 0, 30),
        ("middle_31_100", 30, 100),
        ("early_above_100", 100, np.inf),
    ):
        selected = (actual > lower) & (actual <= upper)
        if selected.any():
            # Reweight within each life bin so long trajectories do not dominate.
            w = engine_weights(frame.loc[selected])
            bins[name] = dict(
                windows=int(selected.sum()),
                engines=int(frame.loc[selected].stream.nunique()),
                mae_cycles=float(np.average(np.abs(error[selected]), weights=w)),
                mean_optimistic_bias_cycles=float(np.average(error[selected], weights=w)),
            )
    display_error = np.maximum(0, prediction) - actual
    return dict(
        life_unit="cycles",
        target_cap=None,
        windows=len(frame),
        engines=int(frame.stream.nunique()),
        mae_cycles=float(np.average(np.abs(error), weights=weights)),
        rmse_cycles=float(np.sqrt(np.average(error**2, weights=weights))),
        mean_optimistic_bias_cycles=float(np.average(error, weights=weights)),
        mean_overestimate_cycles=float(np.average(np.maximum(0, error), weights=weights)),
        negative_raw_predictions=int((prediction < 0).sum()),
        nonnegative_display_mae_cycles=float(np.average(np.abs(display_error), weights=weights)),
        life_bins=bins,
    )


def predict_baseline(model, x, cycles):
    if model["kind"] == "constant":
        return np.full(len(x), model["value_cycles"])
    if model["kind"] == "usage":
        return model["expected_terminal_cycle"] - np.asarray(cycles)
    if model["kind"] == "ridge":
        transformed = (np.asarray(x) - np.array(model["center"])) / np.array(model["scale"])
        return transformed @ np.array(model["coefficients"]) + model["intercept"]
    raise ValueError("Unknown RUL reference")


def fit_baselines(frame, names, cfg):
    x, y = frame[names].to_numpy(), frame.rul_cycles.to_numpy(dtype=float)
    weights = engine_weights(frame)
    center = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - center) ** 2, axis=0, weights=weights))
    scale = np.where(scale <= 1e-10, 1.0, scale)
    transformed = (x - center) / scale
    candidates = dict(
        constant=dict(kind="constant", value_cycles=float(np.average(y, weights=weights))),
        usage=dict(
            kind="usage",
            expected_terminal_cycle=float(frame.groupby("stream").event_cycle.first().mean()),
        ),
    )
    alphas = cfg["rul_training"]["ridge_alphas"]
    if not alphas or len(alphas) > 6 or any(not 0 < a <= 1000 for a in alphas):
        raise ValueError("Bounded Ridge search required")
    for alpha in alphas:
        estimator = Ridge(alpha=alpha)
        with threadpool_limits(limits=cfg["training"]["threads"]):
            estimator.fit(transformed, y, sample_weight=weights)
        model = dict(
            kind="ridge",
            alpha=alpha,
            coefficients=estimator.coef_.tolist(),
            intercept=float(estimator.intercept_),
            center=center.tolist(),
            scale=scale.tolist(),
        )
        if not np.allclose(
            predict_baseline(model, x, frame.cycle),
            estimator.predict(transformed),
            atol=1e-9,
        ):
            raise ValueError("Ridge native/export prediction parity failure")
        candidates[f"ridge_{alpha:g}"] = model
    return candidates


def train_baselines(cfg, output):
    data, preprocessing, provenance = load_rul(cfg)
    names = list(preprocessing.names)
    models = fit_baselines(data["fit"], names, cfg)
    tune = {
        name: regression_metrics(
            data["tune"],
            predict_baseline(model, data["tune"][names].to_numpy(), data["tune"].cycle),
        )
        for name, model in models.items()
    }
    chosen = min(tune, key=lambda name: (tune[name]["mae_cycles"], name))
    report = dict(
        chosen=chosen,
        tune=tune,
        provenance=provenance,
        life_unit="cycles",
        early_life_cap=None,
        short_engine_strategy="abstain_before_cycle_30_no_borrowed_history",
        official_test_accessed=False,
        preprocessing=dict(
            names=names,
            source="data/processed/features/preprocessing.json",
            hash=preprocessing.content_hash,
        ),
        selected_model=models[chosen],
        candidates=models,
    )
    report["bundle_hash"] = content_hash(report)
    write_json(output / "model.json", report)
    write_json(ROOT / "docs/exports/rul_baselines.json", report)
    return report


def load_baseline(folder):
    report = json.loads((folder / "model.json").read_text())
    if (
        content_hash({k: v for k, v in report.items() if k != "bundle_hash"})
        != report["bundle_hash"]
    ):
        raise ValueError("RUL reference integrity mismatch")
    return report


def predict_rul(folder, x, cycles):
    report = load_baseline(folder)
    if report.get("kind") == "xgboost":
        from xgboost import XGBRegressor

        if file_hash(folder / "regressor.json") != report["model_file_hash"]:
            raise ValueError("RUL native model integrity mismatch")
        estimator = XGBRegressor(device="cpu", n_jobs=2)
        estimator.load_model(folder / "regressor.json")
        return estimator.predict(x)
    return predict_baseline(report["selected_model"], x, cycles)
