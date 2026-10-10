"""Controlled synthetic healthy scope and inspectable Isolation Forest artifacts.

Fixture truth establishes training eligibility but is never a model input.
This isolated sensor-model track is not a validated fleet/calendar health model.
"""

import json

import numpy as np
import pandas as pd
import yaml
from fleetiq_evaluation.splits import ROOT, content_hash
from sklearn.ensemble import IsolationForest
from threadpoolctl import threadpool_limits

from fleetiq_training.classification import write_json

SENSORS = ("temperature_c", "pressure_kpa", "vibration_mm_s")
CONTEXT = ("workload", "ambient_c")
UNITS = ("degC", "kPa", "mm/s")


def fixture(cfg, role, *, degrading=False, engines=None):
    """Independent observed trajectories; degradation flag is evaluator-only."""
    spec = cfg["anomaly_training"]
    if role not in spec["seed_offsets"]:
        raise ValueError("Unknown fixture role")
    count = engines or (spec["fit_engines"] if role == "fit" else spec[f"{role}_engines_per_class"])
    if role == "fit" and degrading:
        raise ValueError("Fit scope must be verified zero-damage normal")
    measurement = yaml.safe_load((ROOT / "config/synthetic.yaml").read_text())["measurement"]
    rows = []
    for engine in range(count):
        rng = np.random.default_rng(
            cfg["training"]["seed"] + spec["seed_offsets"][role] + engine + 1000 * degrading
        )
        for hour in range(spec["hours"]):
            workload = rng.uniform(0.3, 1.1)
            ambient = rng.uniform(10, 40)
            damage = max(0, (hour - 12) / 40) if degrading else 0.0
            noise = rng.normal(size=3)
            rows.append(
                dict(
                    stream=f"fixture:{role}:{'degrading' if degrading else 'normal'}:{engine}",
                    hour=hour,
                    workload=workload,
                    ambient_c=ambient,
                    temperature_c=measurement["temperature_base_c"]
                    + measurement["temperature_workload_c"] * workload
                    + 0.3 * (ambient - 25)
                    + measurement["temperature_damage_c"] * damage
                    + measurement["temperature_noise_c"] * noise[0],
                    pressure_kpa=measurement["pressure_base_kpa"]
                    + measurement["pressure_workload_kpa"] * workload
                    - measurement["pressure_damage_kpa"] * damage
                    + measurement["pressure_noise_kpa"] * noise[1],
                    vibration_mm_s=measurement["vibration_base_mm_s"]
                    + measurement["vibration_workload_mm_s"] * workload
                    + measurement["vibration_damage_mm_s"] * damage
                    + measurement["vibration_noise_mm_s"] * noise[2],
                    abnormal=damage >= 0.2,
                    training_normal=not degrading,
                )
            )
    return pd.DataFrame(rows)


def quality(row, units=UNITS):
    if tuple(units) != UNITS:
        return "unit_mismatch"
    values = np.asarray([row[name] for name in (*SENSORS, *CONTEXT)], dtype=float)
    if not np.isfinite(values).all():
        return "missing_essential"
    if not (-100 <= values[0] <= 1000 and 0 <= values[1] <= 2000 and 0 <= values[2] <= 100):
        return "impossible_value"
    if not (0.2 <= values[3] <= 1.2 and 0 <= values[4] <= 50):
        return "out_of_context"
    return "valid"


def fit_context(frame):
    if frame.empty or not frame.training_normal.all() or frame.abnormal.any():
        raise ValueError("Only verified healthy fit observations permitted")
    if any(quality(row) != "valid" for row in frame.to_dict("records")):
        raise ValueError("Invalid healthy training scope")
    x = np.column_stack([np.ones(len(frame)), frame[list(CONTEXT)].to_numpy()])
    y = frame[list(SENSORS)].to_numpy()
    coefficients = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ coefficients
    return dict(
        coefficients=coefficients.tolist(), scale=np.maximum(residual.std(0), 1e-6).tolist()
    )


def vectors(frame, context, window=5):
    """Causal trailing residuals; invalid essential readings abstain, never impute healthy."""
    if window != 5 or frame[["stream", "hour"]].duplicated().any():
        raise ValueError("Unique stream/hour keys and frozen five-hour window required")
    output = []
    for stream, group in frame.groupby("stream", sort=True):
        history, previous = [], None
        for row in group.sort_values("hour").to_dict("records"):
            state = quality(row)
            if previous is not None and row["hour"] != previous + 1:
                history = []
            previous = row["hour"]
            if state != "valid":
                history = []
            else:
                design = np.array([1, *[row[k] for k in CONTEXT]])
                residual = (
                    np.array([row[k] for k in SENSORS]) - design @ np.array(context["coefficients"])
                ) / np.array(context["scale"])
                history.append(residual)
                history = history[-window:]
                if len(history) < window:
                    state = "short_history"
            supported = state == "valid"
            vector = None
            if supported:
                values = np.array(history)
                times = np.arange(window) - (window - 1) / 2
                vector = np.concatenate(
                    [values[-1], values.mean(0), times @ values / (times @ times)]
                ).tolist()
            output.append(dict(stream=stream, hour=row["hour"], quality=state, vector=vector))
    return output


def average_path(n):
    n = np.asarray(n, dtype=float)
    result = np.zeros_like(n)
    result[n == 2] = 1
    mask = n > 2
    result[mask] = 2 * (np.log(n[mask] - 1) + np.euler_gamma) - 2 * (n[mask] - 1) / n[mask]
    return result


def forest_score(artifact, x):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] != 9 or not np.isfinite(x).all():
        raise ValueError("Nine finite causal residual features required")
    depths = np.zeros(len(x))
    for tree in artifact["trees"]:
        left, right = np.array(tree["left"]), np.array(tree["right"])
        feature, threshold = np.array(tree["feature"]), np.array(tree["threshold"])
        nodes, length = np.zeros(len(x), dtype=int), np.zeros(len(x))
        for _ in range(artifact["max_depth"] + 1):
            active = np.flatnonzero(left[nodes] != -1)
            if not len(active):
                break
            current = nodes[active]
            nodes[active] = np.where(
                x[active, feature[current]] <= threshold[current], left[current], right[current]
            )
            length[active] += 1
        if np.any(left[nodes] != -1):
            raise ValueError("Forest depth mismatch")
        depths += length + average_path(np.array(tree["samples"])[nodes])
    return 2 ** (-depths / (len(artifact["trees"]) * average_path(artifact["max_samples"])))


def fit_forest(x, cfg):
    spec = cfg["anomaly_training"]
    if not 16 <= spec["n_estimators"] <= 256 or not 16 <= spec["max_samples"] <= 512:
        raise ValueError("Bounded forest settings required")
    model = IsolationForest(
        n_estimators=spec["n_estimators"],
        max_samples=min(len(x), spec["max_samples"]),
        random_state=cfg["training"]["seed"],
        n_jobs=cfg["training"]["threads"],
        contamination="auto",
    )
    with threadpool_limits(limits=cfg["training"]["threads"]):
        model.fit(x)
    trees = []
    for estimator in model.estimators_:
        tree = estimator.tree_
        trees.append(
            dict(
                left=tree.children_left.tolist(),
                right=tree.children_right.tolist(),
                feature=tree.feature.tolist(),
                threshold=tree.threshold.tolist(),
                samples=tree.n_node_samples.tolist(),
            )
        )
    artifact = dict(
        trees=trees,
        max_depth=max(e.tree_.max_depth for e in model.estimators_),
        max_samples=int(model.max_samples_),
    )
    if not np.allclose(forest_score(artifact, x), -model.score_samples(x), atol=1e-12):
        raise ValueError("Controlled/native Isolation Forest parity failed")
    return artifact


def score_rows(artifact, frame):
    rows = vectors(frame, artifact["context"])
    valid = [r for r in rows if r["vector"] is not None]
    scores = forest_score(artifact["forest"], [r["vector"] for r in valid]) if valid else []
    reference = np.array(artifact["reference"])
    for row, score in zip(valid, scores, strict=True):
        row["raw_score"] = float(score)
        row["normal_reference_percentile"] = float(
            np.searchsorted(reference, score) / len(reference)
        )
        row["anomalous"] = bool(score > artifact["threshold"])
    return rows


def train_anomaly(cfg, output):
    spec = cfg["anomaly_training"]
    if spec["healthy_definition"] != "controlled_zero_damage_no_quality_fault_fixture":
        raise ValueError("Unverified normal definition")
    normal = fixture(cfg, "fit")
    context = fit_context(normal)
    x = np.array([r["vector"] for r in vectors(normal, context) if r["vector"] is not None])
    forest = fit_forest(x, cfg)
    reference = np.sort(forest_score(forest, x))
    tune_normal, tune_degrading = fixture(cfg, "tune"), fixture(cfg, "tune", degrading=True)
    tune = pd.concat([tune_normal, tune_degrading], ignore_index=True)
    artifact = dict(
        model="isolation_forest",
        track="synthetic_sensor_model",
        forest=forest,
        context=context,
        reference=reference.tolist(),
        threshold=1.0,
    )
    rows = score_rows(artifact, tune)
    labels = tune.set_index(["stream", "hour"])
    valid = [r for r in rows if r["vector"] is not None]
    normal_scores = [
        r["raw_score"] for r in valid if labels.loc[(r["stream"], r["hour"]), "training_normal"]
    ]
    candidates = sorted(set(normal_scores + [1.0]))
    limit = spec["false_alert_budget_per_1000_windows"]
    threshold = next(t for t in candidates if 1000 * np.mean(np.array(normal_scores) > t) <= limit)
    positives = [r["raw_score"] for r in valid if labels.loc[(r["stream"], r["hour"]), "abnormal"]]
    artifact["threshold"] = float(threshold)
    report = dict(
        model="isolation_forest",
        threshold=float(threshold),
        score_semantics=spec["score_semantics"],
        healthy_definition=spec["healthy_definition"],
        fit_engines=int(normal.stream.nunique()),
        fit_windows=len(x),
        groups={"fit": sorted(normal.stream.unique()), "tune": sorted(tune.stream.unique())},
        input_hash=content_hash(normal.to_dict("records")),
        config_hash=content_hash(spec),
        final_test_accessed=False,
        scope="controlled synthetic fixture only",
        tune=dict(
            false_alerts_per_1000_windows=float(
                1000 * np.mean(np.array(normal_scores) > threshold)
            ),
            abnormal_window_recall=float(np.mean(np.array(positives) > threshold)),
        ),
    )
    body = dict(artifact=artifact, report=report)
    body["bundle_hash"] = content_hash(body)
    write_json(output / "model.json", body)
    write_json(ROOT / "docs/exports/anomaly_training.json", report)
    return report


def load_anomaly(folder):
    body = json.loads((folder / "model.json").read_text())
    if content_hash({k: v for k, v in body.items() if k != "bundle_hash"}) != body["bundle_hash"]:
        raise ValueError("Anomaly bundle integrity mismatch")
    return body
