"""Train-only logistic reference and controlled, track-bound model artifacts."""

import hashlib
import json
import time

import numpy as np
import pandas as pd
import sklearn
import yaml
from fleetiq_data.targets import training_cycle_targets
from fleetiq_evaluation.splits import (
    ROOT,
    benchmark_splits,
    content_hash,
    load_benchmark,
    load_config,
)
from fleetiq_features.pipeline import PipelineFit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True, allow_nan=False))


def experiment(path):
    cfg = yaml.safe_load(path.read_text())
    if (
        cfg["sources"]["benchmark"] != "data/processed/cmapss/train.parquet"
        or cfg["evaluation"]["horizon_cycles"] != 30
    ):
        raise ValueError(
            "Only approved NASA training observations and 30-cycle failure task supported"
        )
    if cfg["training"]["threads"] not in (1, 2, 3, 4):
        raise ValueError("Bounded CPU thread count required")
    return cfg


def load_development(cfg, *, root=ROOT, roles=("fit", "tune")):
    if set(roles) - {"fit", "tune", "calibration"}:
        raise ValueError("Development loader cannot access final test")
    frame = load_benchmark(root)
    splits = benchmark_splits(frame, load_config(root / cfg["splits"]))
    folder = root / "data/processed/features"
    fit = PipelineFit.load(folder / "preprocessing.json")
    expected = {str(value.rsplit(":", 1)[1]) for value in splits["groups"]["fit"]}
    if fit.track != "cmapss_benchmark" or set(fit.training_groups) != expected:
        raise ValueError("Preprocessing must have exactly the frozen fit engines")
    units = [int(value.rsplit(":", 1)[1]) for role in roles for value in splits["groups"][role]]
    columns = [
        "unit_id",
        "cycle",
        "stream",
        "schema_hash",
        "fit_hash",
        "input_hash",
        "supported",
        *fit.names,
    ]
    features = pd.read_parquet(
        folder / "snapshots.parquet", columns=columns, filters=[("unit_id", "in", units)]
    )
    if (
        features[["unit_id", "cycle"]].duplicated().any()
        or set(features.schema_hash) != {fit.schema_hash}
        or set(features.fit_hash) != {fit.content_hash}
    ):
        raise ValueError("Snapshot identity/schema/fit mismatch")
    labels = training_cycle_targets(frame.loc[frame.unit_id.isin(units)], horizon_cycles=30)
    merged = features.merge(labels, on=["unit_id", "cycle"], validate="one_to_one")
    if len(merged) != len(features):
        raise ValueError("Unmapped observed feature keys")
    data = {}
    for role in roles:
        ids = set(splits["groups"][role])
        subset = merged.loc[merged.stream.isin(ids) & merged.eligible & merged.supported].copy()
        if role != "fit":
            subset = subset.loc[
                (subset.cycle >= cfg["evaluation"]["minimum_cycle"])
                & (
                    (subset.cycle - cfg["evaluation"]["minimum_cycle"])
                    % cfg["evaluation"]["stride_cycles"]
                    == 0
                )
            ]
        subset = subset.sort_values(["unit_id", "cycle"])
        if subset.empty or not np.isfinite(subset[list(fit.names)].to_numpy()).all():
            raise ValueError("Finite eligible development observations required")
        data[role] = subset
    anchors = {
        role: content_hash(part[["stream", "cycle", "input_hash"]].to_dict("records"))
        for role, part in data.items()
    }
    provenance = dict(
        split_hash=splits["manifest_hash"],
        input_hash=splits["input_hash"],
        preprocessing_hash=fit.content_hash,
        schema_hash=fit.schema_hash,
        anchors=anchors,
        vectors={
            role: hashlib.sha256(
                np.ascontiguousarray(part[list(fit.names)].to_numpy(), dtype="<f8").tobytes()
            ).hexdigest()
            for role, part in data.items()
        },
        labels={
            role: content_hash(
                part[["stream", "cycle", "event_cycle", "failure_within_horizon"]].to_dict(
                    "records"
                )
            )
            for role, part in data.items()
        },
        groups={role: splits["groups"][role] for role in roles},
        names=list(fit.names),
        official_test_accessed=False,
    )
    return data, fit, provenance


def sigmoid(values):
    return 1 / (1 + np.exp(-np.clip(values, -709, 709)))


def fit_logistic(x, y, cfg):
    if set(np.unique(y)) != {0, 1}:
        raise ValueError("Failure fitting requires both natural outcome classes")
    scaler = StandardScaler().fit(x)
    model = LogisticRegression(
        **cfg["training"]["logistic"],
        solver="lbfgs",
        class_weight=cfg["training"]["class_weight"],
        random_state=cfg["training"]["seed"],
    )
    with threadpool_limits(limits=cfg["training"]["threads"]):
        model.fit(scaler.transform(x), y)
    if model.n_iter_[0] >= cfg["training"]["logistic"]["max_iter"]:
        raise ValueError("Logistic solver did not converge; do not proceed to boosting")
    artifact = dict(
        model="logistic",
        coefficients=model.coef_[0].tolist(),
        intercept=float(model.intercept_[0]),
        center=scaler.mean_.tolist(),
        scale=scaler.scale_.tolist(),
    )
    assert np.allclose(
        predict_controlled(artifact, x), model.predict_proba(scaler.transform(x))[:, 1], atol=1e-10
    )
    return artifact


def predict_controlled(artifact, x):
    x = np.asarray(x, dtype=float)
    if artifact["model"] == "logistic":
        z = (x - np.array(artifact["center"])) / np.array(artifact["scale"])
        return sigmoid(z @ np.array(artifact["coefficients"]) + artifact["intercept"])
    if artifact["model"] == "random_forest":
        x = x.astype(np.float32)
        if not np.isfinite(x).all():
            raise ValueError("Forest input exceeds native float32 range")
        result = np.zeros(len(x))
        for tree in artifact["trees"]:
            left, right = np.array(tree["left"]), np.array(tree["right"])
            feature, threshold, probability = (
                np.array(tree["feature"]),
                np.array(tree["threshold"]),
                np.array(tree["positive"]),
            )
            nodes = np.zeros(len(x), dtype=int)
            for _ in range(artifact["max_depth"] + 1):
                active = np.flatnonzero(left[nodes] != -1)
                if not len(active):
                    break
                current = nodes[active]
                nodes[active] = np.where(
                    x[active, feature[current]] <= threshold[current], left[current], right[current]
                )
            if np.any(left[nodes] != -1):
                raise ValueError("Invalid forest depth")
            result += probability[nodes]
        return result / len(artifact["trees"])
    raise ValueError("Unknown controlled model type")


def load_model(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    if (
        content_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
        != manifest["manifest_hash"]
    ):
        raise ValueError("Model manifest integrity mismatch")
    if file_hash(folder / "model.json") != manifest["model_hash"]:
        raise ValueError("Native model integrity mismatch")
    if manifest["model"] == "xgboost":
        from xgboost import XGBClassifier

        model = XGBClassifier(n_jobs=manifest["threads"], device="cpu")
        model.load_model(folder / "model.json")
        return manifest, lambda x: model.predict_proba(np.asarray(x))[:, 1]
    controlled = json.loads((folder / "model.json").read_text())
    return manifest, lambda x: predict_controlled(controlled, x)


def score_bundle(folder, x, *, track, horizon_cycles, schema_hash):
    manifest, predict = load_model(folder)
    if (
        track != manifest["track"]
        or horizon_cycles != manifest["horizon_cycles"]
        or schema_hash != manifest["provenance"]["schema_hash"]
    ):
        raise ValueError("Unsupported track, horizon or feature schema")
    x = np.asarray(x, dtype=float)
    if (
        x.ndim != 2
        or x.shape[1] != len(manifest["provenance"]["names"])
        or not np.isfinite(x).all()
    ):
        raise ValueError("Finite ordered model feature layout required")
    values = predict(x)
    return dict(scores=values, probability_supported=False, life_unit="cycles", horizon_cycles=30)


def metrics(rows, scores, threshold, *, exposure=None):
    y = rows.failure_within_horizon.astype(int).to_numpy()
    scores = np.asarray(scores)
    if (
        len(y) != len(scores)
        or not len(y)
        or not np.isfinite(scores).all()
        or np.any((scores < 0) | (scores > 1))
    ):
        raise ValueError("Finite bounded scores and matching eligible outcomes required")
    alerts = scores >= threshold
    tp, fp, fn = (
        int(np.sum(alerts & (y == 1))),
        int(np.sum(alerts & (y == 0))),
        int(np.sum(~alerts & (y == 1))),
    )
    positive_engines = set(rows.loc[y == 1, "stream"])
    detected = set(rows.loc[alerts & (y == 1), "stream"])
    lead = rows.loc[alerts & (y == 1)].groupby("stream").rul_cycles.max()
    if exposure is None:
        exposure = float(
            sum(
                max(0, group.event_cycle.iloc[0] - group.cycle.min())
                for _, group in rows.groupby("stream")
            )
        )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    two_classes = len(np.unique(y)) == 2
    return dict(
        samples=len(y),
        positives=int(y.sum()),
        engines=rows.stream.nunique(),
        average_precision=float(average_precision_score(y, scores)) if two_classes else None,
        roc_auc=float(roc_auc_score(y, scores)) if two_classes else None,
        precision=precision,
        recall=recall,
        f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        event_recall=len(detected) / len(positive_engines) if positive_engines else None,
        independent_positive_events=len(positive_engines),
        detected_events=len(detected),
        false_alerts=fp,
        exposure_cycles=exposure,
        false_alerts_per_1000_cycles=fp / exposure * 1000 if exposure > 0 else None,
        useful_warning_lead_cycles_median=float(lead.median()) if len(lead) else None,
        uncalibrated_score_brier=float(brier_score_loss(y, scores)),
        uncalibrated_score_log_loss=float(log_loss(y, scores, labels=[0, 1])),
        threshold=float(threshold),
    )


def select_threshold(rows, scores, budget):
    candidates = np.unique(np.r_[0.0, scores, np.nextafter(1.0, 2.0)])
    ranked = []
    for threshold in candidates:
        result = metrics(rows, scores, threshold)
        if (
            result["false_alerts_per_1000_cycles"] is not None
            and result["false_alerts_per_1000_cycles"] <= budget
        ):
            ranked.append(
                ((result["event_recall"] or 0, result["precision"] or 0, float(threshold)), result)
            )
    if not ranked:
        raise ValueError("No threshold meets declared alert burden")
    return max(ranked, key=lambda item: item[0])[1]


def engine_bootstrap(rows, scores, threshold, *, seed, repetitions, endpoint_only=False):
    engines = sorted(rows.stream.unique())
    if len(engines) < 2:
        return dict(supported=False, reason="insufficient_independent_engines")
    rng = np.random.default_rng(seed)
    indices = {engine: np.flatnonzero(rows.stream.to_numpy() == engine) for engine in engines}
    results = []
    for _ in range(repetitions):
        parts, sampled_scores = [], []
        for draw, engine in enumerate(rng.choice(engines, len(engines), replace=True)):
            index = indices[engine]
            part = rows.iloc[index].copy()
            part["stream"] = f"bootstrap:{draw}"
            parts.append(part)
            sampled_scores.extend(np.asarray(scores)[index])
        results.append(
            metrics(
                pd.concat(parts, ignore_index=True),
                sampled_scores,
                threshold,
                exposure=0 if endpoint_only else None,
            )
        )
    intervals = {}
    for key in (
        "average_precision",
        "event_recall",
        "precision",
        "false_alerts_per_1000_cycles",
        "useful_warning_lead_cycles_median",
    ):
        values = [row[key] for row in results if row[key] is not None]
        intervals[key] = np.quantile(values, [0.025, 0.975]).tolist() if values else None
    return dict(
        supported=True,
        resampling_unit="whole_engine_event",
        independent_engines=len(engines),
        repetitions=repetitions,
        seed=seed,
        confidence_level=0.95,
        percentile_intervals=intervals,
    )


def train(cfg, output, *, model="logistic", track="cmapss_benchmark"):
    if track != "cmapss_benchmark":
        raise ValueError(
            "Synthetic risk remains unsupported; NASA engines cannot represent hydraulic risk"
        )
    data, fit, provenance = load_development(cfg)
    x = data["fit"][list(fit.names)].to_numpy()
    y = data["fit"].failure_within_horizon.astype(int).to_numpy()
    tune_x = data["tune"][list(fit.names)].to_numpy()
    output.mkdir(parents=True, exist_ok=True)
    settings = cfg["training"].get(model, {})
    if model == "logistic":
        payload = fit_logistic(x, y, cfg)
        write_json(output / "model.json", payload)

        def predict(values):
            return predict_controlled(payload, values)
    elif model == "random_forest":
        from fleetiq_training.random_forest import fit_forest

        payload = fit_forest(x, y, cfg)
        write_json(output / "model.json", payload)

        def predict(values):
            return predict_controlled(payload, values)
    elif model == "xgboost":
        from fleetiq_training.xgb_failure import fit_xgboost

        predictor, settings = fit_xgboost(
            x, y, tune_x, data["tune"].failure_within_horizon.astype(int).to_numpy(), cfg
        )
        predictor.save_model(output / "model.json")

        def predict(values):
            return predictor.predict_proba(values)[:, 1]
    else:
        raise ValueError("Unsupported failure estimator")
    scores = predict(tune_x)
    chosen = select_threshold(
        data["tune"], scores, cfg["evaluation"]["false_alert_budget_per_1000_cycles"]
    )
    manifest = dict(
        version="failure-model-v1",
        task="failure",
        model=model,
        track=track,
        life_unit="cycles",
        horizon_cycles=30,
        seed=cfg["training"]["seed"],
        threads=cfg["training"]["threads"],
        class_weight=cfg["training"]["class_weight"],
        settings=settings,
        model_hash=file_hash(output / "model.json"),
        provenance=provenance,
        evaluation_protocol=cfg["evaluation"],
        threshold=chosen["threshold"],
        threshold_role="tune_only",
        sklearn_version=sklearn.__version__,
        probability_deployment_allowed=False,
    )
    manifest["manifest_hash"] = content_hash(manifest)
    write_json(output / "manifest.json", manifest)
    fit.save(output / "preprocessing.json")
    loaded, deployed = load_model(output)
    if not np.allclose(scores, deployed(tune_x), atol=1e-9):
        raise ValueError("Controlled/native save-load parity failed")
    start = time.perf_counter()
    deployed(tune_x)
    elapsed = time.perf_counter() - start
    report = dict(
        model=model,
        manifest_hash=loaded["manifest_hash"],
        provenance=provenance,
        fit=metrics(data["fit"], predict(x), chosen["threshold"]),
        tune=chosen,
        bootstrap=engine_bootstrap(
            data["tune"],
            scores,
            chosen["threshold"],
            seed=cfg["training"]["seed"],
            repetitions=cfg["evaluation"]["bootstrap_engines"],
        ),
        measured_inference_batch_seconds=elapsed,
        batch_samples=len(tune_x),
        final_test_accessed=False,
        calibration_not_used_for_selection=True,
    )
    write_json(ROOT / f"docs/exports/{model}_tuning.json", report)
    if model == "random_forest":
        from fleetiq_training.random_forest import record_comparison

        record_comparison()
    if model == "xgboost":
        from fleetiq_training.xgb_failure import record_selection

        record_selection(cfg)
    return manifest, report
