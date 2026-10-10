"""Independent hourly simulator experiment; evaluator truth never enters features."""

import copy
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fleetiq_data.contracts import SyntheticObservation
from fleetiq_data.synthetic.degradation import generate_trajectory
from fleetiq_data.synthetic.failures import sample_failures
from fleetiq_evaluation.splits import ROOT, content_hash
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    precision_recall_fscore_support,
)
from xgboost import XGBClassifier, XGBRegressor

from fleetiq_training import anomaly
from fleetiq_training.calibration import fit_sigmoid, probabilities, reliability
from fleetiq_training.classification import file_hash, fit_logistic, predict_controlled, write_json
from fleetiq_training.rul_baseline import engine_weights
from fleetiq_training.rul_intervals import calibrate_engine_intervals

NAMES = [
    f"{stat}.{sensor}"
    for stat in ("current", "mean5", "slope5")
    for sensor in ("temperature", "pressure", "vibration")
] + ["observed.workload", "observed.ambient_c", "observed.age_hours", "observed.operating_hours"]
SCOPE = "synthetic_sensor_model:hourly_stochastic_failure:uncapped"


def features(observed, context):
    if set(observed.columns) - set(SyntheticObservation.model_fields):
        raise ValueError("Only observed synthetic-engine-v1 channels accepted; truth forbidden")
    if observed.empty:
        raise ValueError("Observed trajectory required")
    rows = [SyntheticObservation.model_validate(r) for r in observed.to_dict("records")]
    frame = pd.DataFrame(
        [
            dict(
                stream=str(r.installation_id),
                hour=r.operating_hours,
                workload=r.workload,
                ambient_c=r.ambient_temperature_c,
                temperature_c=r.temperature_c,
                pressure_kpa=r.oil_pressure_kpa,
                vibration_mm_s=r.vibration_mm_s,
                age_hours=r.component_age_hours,
            )
            for r in rows
        ]
    )
    if len(frame.stream.unique()) != 1 or not np.array_equal(
        frame.sort_values("hour").hour, np.arange(len(frame))
    ):
        raise ValueError("One contiguous installation history from hour zero required")
    original = frame.set_index(["stream", "hour"])
    results = []
    for row in anomaly.vectors(frame, context):
        observed_row = original.loc[(row["stream"], row["hour"])]
        vector = (
            None
            if row["vector"] is None
            else row["vector"]
            + [
                float(observed_row.workload),
                float(observed_row.ambient_c),
                float(observed_row.age_hours),
                float(row["hour"]),
            ]
        )
        results.append(
            dict(stream=row["stream"], hour=row["hour"], vector=vector, quality=row["quality"])
        )
    return results


def role_data(cfg, role, context):
    spec = cfg["synthetic_hour_training"]
    if role not in spec["roles"]:
        raise ValueError("Unknown independent experiment role")
    definition = spec["roles"][role]
    rows, streams, exposure = [], [], 0
    for engine in range(definition["engines"]):
        seed = cfg["training"]["seed"] + definition["seed_offset"] + engine
        rng = np.random.default_rng(seed)
        trajectory = generate_trajectory(
            seed=seed,
            hours=spec["hours"],
            component_serial=f"SYNTH-{role}-{engine}",
            installed_at=datetime.fromisoformat(definition["installed_at"]),
            initial_age_hours=float(rng.uniform(0, 500)),
            initial_damage=float(rng.uniform(0, 1)),
            regime="degrading" if engine % 2 else "normal",
        )
        # Evaluator data crosses only this label construction boundary.
        failure = sample_failures(trajectory.evaluator, seed=seed).iloc[0]
        observed = trajectory.observed
        end = spec["hours"] - 1
        event_hour = (
            None
            if failure.event_time is None
            else int(
                (
                    pd.Timestamp(failure.event_time) - pd.Timestamp(observed.measured_at.iloc[0])
                ).total_seconds()
                / 3600
            )
        )
        stop = event_hour if event_hour is not None else end
        observed = observed.iloc[: stop + 1]
        extracted = features(observed, context)
        stream = str(observed.installation_id.iloc[0])
        streams.append(stream)
        exposure += max(0, stop - spec["minimum_hour"])
        for result in extracted:
            hour = int(result["hour"])
            if (
                result["vector"] is None
                or hour < spec["minimum_hour"]
                or (hour - spec["minimum_hour"]) % spec["stride_hours"]
                or hour >= stop
            ):
                continue
            # Censored histories cannot manufacture a negative label without 24h follow-up.
            if event_hour is None and hour + spec["horizon_hours"] > end:
                continue
            rows.append(
                dict(
                    stream=stream,
                    hour=hour,
                    life_unit="operating_hours",
                    rul_hours=None if event_hour is None else event_hour - hour,
                    failure_within_horizon=event_hour is not None
                    and event_hour - hour <= spec["horizon_hours"],
                    vector=result["vector"],
                )
            )
    frame = pd.DataFrame(rows)
    if frame.empty or frame.stream.nunique() < 20 or frame.failure_within_horizon.nunique() != 2:
        raise ValueError("Insufficient independent native-hour fitting/evaluation support")
    return frame, dict(
        streams=sorted(streams),
        eligible_engines=int(frame.stream.nunique()),
        exposure_hours=exposure,
        seed_offset=definition["seed_offset"],
        installed_at=definition["installed_at"],
        censored_rul_excluded=True,
    )


def risk_metrics(frame, scores, threshold, exposure):
    y = frame.failure_within_horizon.astype(int).to_numpy()
    alert = np.asarray(scores) >= threshold
    p, r, f, _ = precision_recall_fscore_support(y, alert, average="binary", zero_division=0)
    positive = set(frame.loc[y == 1, "stream"])
    detected = set(frame.loc[alert & (y == 1), "stream"])
    return dict(
        precision=float(p),
        recall=float(r),
        f1=float(f),
        accuracy=float(accuracy_score(y, alert)),
        average_precision=float(average_precision_score(y, scores)),
        brier=float(brier_score_loss(y, scores)),
        event_recall=len(detected) / len(positive) if positive else None,
        independent_positive_events=len(positive),
        false_alert_windows=int(np.sum(alert & (y == 0))),
        false_alerts_per_1000_hours=1000 * int(np.sum(alert & (y == 0))) / exposure,
        exposure_hours=exposure,
        samples=len(frame),
        threshold=float(threshold),
        confusion=dict(
            tp=int(np.sum(alert & (y == 1))),
            fp=int(np.sum(alert & (y == 0))),
            fn=int(np.sum(~alert & (y == 1))),
            tn=int(np.sum(~alert & (y == 0))),
        ),
    )


def hour_metrics(frame, prediction):
    error = np.asarray(prediction) - frame.rul_hours.to_numpy(dtype=float)
    weights = engine_weights(frame)
    return dict(
        life_unit="operating_hours",
        engines=int(frame.stream.nunique()),
        windows=len(frame),
        mae_hours=float(np.average(abs(error), weights=weights)),
        rmse_hours=float(np.sqrt(np.average(error**2, weights=weights))),
        optimistic_bias_hours=float(np.average(error, weights=weights)),
    )


def train(cfg, output):
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / "artifacts/bundles").resolve()) or output.exists():
        raise ValueError("New immutable bundle directory under artifacts/bundles required")
    spec = cfg["synthetic_hour_training"]
    if (spec["track"], spec["horizon_hours"], spec["life_unit"]) != (
        "synthetic_sensor_model",
        24,
        "operating_hours",
    ):
        raise ValueError("Frozen separate synthetic-hour contract required")
    offsets = [r["seed_offset"] for r in spec["roles"].values()]
    if (
        len(set(offsets)) != 4
        or min(offsets) < 100000
        or any(abs(a - b) < 10000 for i, a in enumerate(offsets) for b in offsets[i + 1 :])
    ):
        raise ValueError("Disjoint independent seeds required")
    output.mkdir(parents=True)
    fresh = copy.deepcopy(cfg)
    fresh["training"]["seed"] += spec["healthy_seed_offset"]
    fresh["anomaly_training"]["seed_offsets"] = dict(fit=0, tune=10000, evaluation=20000)
    normal, tune_normal = anomaly.fixture(fresh, "fit"), anomaly.fixture(fresh, "tune")
    context = anomaly.fit_context(normal)
    normal_vectors = np.array(
        [r["vector"] for r in anomaly.vectors(normal, context) if r["vector"] is not None]
    )
    forest = anomaly.fit_forest(normal_vectors, fresh)
    tune_scores = anomaly.forest_score(
        forest,
        [r["vector"] for r in anomaly.vectors(tune_normal, context) if r["vector"] is not None],
    )
    anomaly_threshold = float(np.quantile(tune_scores, 0.995, method="higher"))
    fit, fit_info = role_data(cfg, "fit", context)
    tune, tune_info = role_data(cfg, "tune", context)
    x, xt = np.array(fit.vector.tolist()), np.array(tune.vector.tolist())
    logistic = fit_logistic(x, fit.failure_within_horizon.astype(int), cfg)
    logistic_scores = predict_controlled(logistic, xt)
    candidates = []
    for depth in spec["xgboost_depths"]:
        classifier = XGBClassifier(
            n_estimators=160,
            max_depth=depth,
            learning_rate=0.05,
            tree_method="hist",
            device="cpu",
            n_jobs=2,
            random_state=cfg["training"]["seed"],
        )
        y = fit.failure_within_horizon.astype(int).to_numpy()
        balanced = np.where(y, len(y) / (2 * y.sum()), len(y) / (2 * (len(y) - y.sum())))
        classifier.fit(x, y, sample_weight=engine_weights(fit) * balanced)
        for weight in (0.0, 0.5, 1.0):
            scores = weight * classifier.predict_proba(xt)[:, 1] + (1 - weight) * logistic_scores
            # Threshold selection sees tune only. Raw alert count is reported separately from event recall.
            choices = []
            actual = tune.failure_within_horizon.to_numpy(dtype=bool)
            event_max = (
                pd.Series(scores[actual], index=tune.loc[actual, "stream"])
                .groupby(level=0)
                .max()
                .to_numpy()
            )
            for threshold in np.unique(np.r_[scores, np.nextafter(1.0, 2.0)]):
                alerts = scores >= threshold
                tp, fp = int(np.sum(alerts & actual)), int(np.sum(alerts & ~actual))
                if (
                    1000 * fp / tune_info["exposure_hours"]
                    <= spec["false_alert_budget_per_1000_hours"]
                ):
                    choices.append(
                        (
                            float(np.mean(event_max >= threshold)),
                            tp / (tp + fp) if tp + fp else 0,
                            float(threshold),
                        )
                    )
            threshold = max(choices)[2]
            best = risk_metrics(tune, scores, threshold, tune_info["exposure_hours"])
            candidates.append((best, classifier, weight, depth))
    selected, classifier, weight, depth = max(
        candidates,
        key=lambda c: (
            c[0]["event_recall"] or 0,
            c[0]["average_precision"],
            c[0]["precision"],
            -c[3],
        ),
    )
    classifier.save_model(output / "classifier.json")
    write_json(output / "logistic.json", logistic)

    def score(values):
        return weight * classifier.predict_proba(values)[:, 1] + (1 - weight) * predict_controlled(
            logistic, values
        )

    fit_rul, tune_rul = (
        fit.loc[fit.rul_hours.notna()].copy(),
        tune.loc[tune.rul_hours.notna()].copy(),
    )
    xr, xrt = np.array(fit_rul.vector.tolist()), np.array(tune_rul.vector.tolist())
    center, scale = xr.mean(0), np.maximum(xr.std(0), 1e-6)
    ridge = Ridge(alpha=10).fit(
        (xr - center) / scale, fit_rul.rul_hours, sample_weight=engine_weights(fit_rul)
    )
    ridge_artifact = dict(
        center=center.tolist(),
        scale=scale.tolist(),
        coefficients=ridge.coef_.tolist(),
        intercept=float(ridge.intercept_),
    )
    rul_candidates = [
        (hour_metrics(tune_rul, ridge.predict((xrt - center) / scale)), "ridge", ridge)
    ]
    for d in spec["xgboost_depths"]:
        regressor = XGBRegressor(
            n_estimators=160,
            max_depth=d,
            learning_rate=0.05,
            tree_method="hist",
            device="cpu",
            n_jobs=2,
            random_state=cfg["training"]["seed"],
        )
        regressor.fit(xr, fit_rul.rul_hours, sample_weight=engine_weights(fit_rul))
        rul_candidates.append(
            (hour_metrics(tune_rul, regressor.predict(xrt)), "xgboost", regressor)
        )
    rul_selected, rul_kind, rul_model = min(
        rul_candidates, key=lambda c: (c[0]["mae_hours"], c[0]["rmse_hours"])
    )
    if rul_kind == "xgboost":
        rul_model.save_model(output / "regressor.json")
    write_json(output / "ridge.json", ridge_artifact)

    def remaining(values):
        return rul_model.predict(values if rul_kind == "xgboost" else (values - center) / scale)

    # Models/thresholds are frozen before calibration and final seed namespaces are generated.
    frozen_selection = dict(
        risk=selected,
        classifier_depth=depth,
        xgboost_weight=weight,
        rul=rul_selected,
        rul_kind=rul_kind,
    )
    selection_hash = content_hash(frozen_selection)
    cal, cal_info = role_data(cfg, "calibration", context)
    if set(fit_info["streams"]) & set(cal_info["streams"]) or set(tune_info["streams"]) & set(
        cal_info["streams"]
    ):
        raise ValueError("Independent engine split violated")
    calibration = fit_sigmoid(
        score(np.array(cal.vector.tolist())),
        cal,
        fit_engines=fit_info["streams"],
        tune_engines=tune_info["streams"],
        calibration_engines=cal_info["streams"],
        minimum_events=20,
        seed=cfg["training"]["seed"],
        anchor_column="hour",
    )
    cal_rul = cal.loc[cal.rul_hours.notna()].copy()
    intervals = calibrate_engine_intervals(
        cal_rul,
        remaining(np.array(cal_rul.vector.tolist())),
        fit_engines=fit_info["streams"],
        tune_engines=tune_info["streams"],
        calibration_engines=sorted(cal_rul.stream.unique()),
        life_unit="operating_hours",
        scope=SCOPE,
    )
    test, test_info = role_data(cfg, "test", context)
    if any(
        set(test_info["streams"]) & set(info["streams"]) for info in (fit_info, tune_info, cal_info)
    ):
        raise ValueError("Final engines overlap development")
    test_scores = score(np.array(test.vector.tolist()))
    test_rul = test.loc[test.rul_hours.notna()].copy()
    prediction = remaining(np.array(test_rul.vector.tolist()))
    risk_report = risk_metrics(
        test, test_scores, selected["threshold"], test_info["exposure_hours"]
    )
    calibrated = probabilities(test_scores, calibration)
    risk_report["calibrated_brier"] = (
        float(brier_score_loss(test.failure_within_horizon, calibrated["probability"]))
        if calibrated["supported"]
        else None
    )
    risk_report["reliability"] = (
        reliability(test, calibrated["probability"]) if calibrated["supported"] else None
    )
    rul_report = hour_metrics(test_rul, prediction)
    radius = intervals["candidate_radius_hours"]
    rul_report["diagnostic_interval_coverage"] = (
        float(np.mean(abs(np.maximum(0, prediction) - test_rul.rul_hours) <= radius))
        if radius is not None
        else None
    )
    rul_report["diagnostic_mean_width_hours"] = (
        float(
            np.mean(
                np.maximum(0, prediction)
                + radius
                - np.maximum(0, np.maximum(0, prediction) - radius)
            )
        )
        if radius is not None
        else None
    )
    report = dict(
        scope=SCOPE,
        fictional=True,
        selection=frozen_selection,
        selection_hash=selection_hash,
        final_selection_hash=content_hash(frozen_selection),
        roles=dict(fit=fit_info, tune=tune_info, calibration=cal_info, test=test_info),
        risk=risk_report,
        rul=rul_report,
        calibration=calibration,
        intervals=intervals,
        anomaly=dict(
            threshold=anomaly_threshold,
            healthy_fit_engines=int(normal.stream.nunique()),
            tune_normal_false_flags_per_1000=float(1000 * np.mean(tune_scores > anomaly_threshold)),
        ),
        probability_display_enabled=False,
        interval_display_enabled=False,
        limitations=[
            "stochastic simulator hazard creates irreducible event-time uncertainty",
            "RUL conditions on uncensored failures; censored engines excluded",
            "not aircraft maintenance evidence",
            "raw alert windows are correlated, not independent review events",
        ],
    )
    write_json(
        output / "preprocessing.json",
        dict(
            names=NAMES,
            context=context,
            fit_streams=fit_info["streams"],
            feature_version="synthetic-hours-features-v1",
        ),
    )
    write_json(output / "anomaly.json", dict(forest=forest, threshold=anomaly_threshold))
    write_json(output / "selection.json", frozen_selection)
    write_json(output / "calibration.json", calibration)
    write_json(output / "intervals.json", intervals)
    write_json(output / "evaluation.json", report)
    manifest = dict(
        version="fleetiq-synthetic-hours-v1",
        track="synthetic_sensor_model",
        schema_version="synthetic-engine-v1",
        horizon_hours=24,
        life_unit="operating_hours",
        files={p.name: file_hash(p) for p in output.glob("*.json")},
        feature_names=NAMES,
        schema_hash=content_hash(NAMES),
        probability_display_enabled=False,
        interval_display_enabled=False,
        selection_hash=selection_hash,
        config_hash=content_hash(spec),
        dependencies=file_hash(ROOT / "uv.lock"),
        sources={
            name: file_hash(ROOT / name)
            for name in ("ml/training/fleetiq_training/synthetic_track.py", "config/synthetic.yaml")
        },
        applicability="fictional independent hourly sensor-model generator only; not imported fleet telemetry",
    )
    write_json(output / "manifest.json", manifest)
    digest = file_hash(output / "manifest.json")
    write_json(
        ROOT / "docs/exports/synthetic_evaluation.json",
        report | dict(bundle_path=output.relative_to(ROOT).as_posix(), approved_digest=digest),
    )
    return digest, report
