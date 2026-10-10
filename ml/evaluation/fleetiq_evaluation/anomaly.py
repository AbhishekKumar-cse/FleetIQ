"""Tune-only persistence selection; independent synthetic robustness evidence."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from fleetiq_training.anomaly import fixture, load_anomaly, score_rows
from fleetiq_training.classification import experiment, write_json

from fleetiq_evaluation.splits import ROOT, content_hash


def persistent_alerts(rows, consecutive, cooldown_hours):
    if consecutive not in (2, 3) or cooldown_hours not in (6, 12):
        raise ValueError("Bounded tune-only persistence settings required")
    states, output, seen = {}, [], set()
    for row in sorted(rows, key=lambda r: (r["stream"], r["hour"])):
        key = (row["stream"], row["hour"])
        if key in seen:
            raise ValueError("Duplicate evidence cannot inflate persistence")
        seen.add(key)
        state = states.setdefault(row["stream"], dict(run=0, previous=None, last_alert=None))
        if state["previous"] is not None and row["hour"] != state["previous"] + 1:
            state["run"] = 0
        state["previous"] = row["hour"]
        supported = row["quality"] == "valid"
        anomalous = supported and row.get("anomalous", False)
        state["run"] = state["run"] + 1 if anomalous else 0
        cooled = state["last_alert"] is None or row["hour"] - state["last_alert"] >= cooldown_hours
        alert = bool(anomalous and state["run"] >= consecutive and cooled)
        if alert:
            state["last_alert"] = row["hour"]
        output.append(
            dict(
                stream=row["stream"],
                hour=row["hour"],
                quality=row["quality"],
                state="abstain"
                if not supported
                else "anomalous"
                if anomalous
                else "normal_evidence",
                review_alert=alert,
                diagnosis=None,
            )
        )
    return output


def metrics(alerts, frame):
    normal = set(frame.loc[frame.training_normal, "stream"])
    supported_normal = [r for r in alerts if r["stream"] in normal and r["quality"] == "valid"]
    false = sum(r["review_alert"] for r in supported_normal)
    delays, missed = [], []
    early = 0
    for stream, group in frame.loc[~frame.training_normal].groupby("stream"):
        abnormal = group.loc[group.abnormal]
        if abnormal.empty:
            continue
        onset = int(abnormal.hour.min())
        matching = [r["hour"] for r in alerts if r["stream"] == stream and r["review_alert"]]
        early += sum(hour < onset for hour in matching)
        detections = [hour for hour in matching if hour >= onset]
        if detections:
            delays.append(min(detections) - onset)
        else:
            missed.append(stream)
    n = len(delays) + len(missed)
    return dict(
        false_alerts=false,
        normal_exposure_hours=len(supported_normal),
        false_alerts_per_1000_normal_hours=1000 * false / max(1, len(supported_normal)),
        events=n,
        detected_events=len(delays),
        event_recall=len(delays) / max(1, n),
        mean_detection_delay_hours=float(np.mean(delays)) if delays else None,
        early_degradation_alerts=early,
        missed_streams=missed,
    )


def evaluate_anomaly(cfg, folder, policy):
    bundle = load_anomaly(folder)
    tune = pd.concat(
        [fixture(cfg, "tune"), fixture(cfg, "tune", degrading=True)], ignore_index=True
    )
    if sorted(tune.stream.unique()) != bundle["report"]["groups"]["tune"]:
        raise ValueError("Anomaly tuning group mismatch")
    settings = policy["anomaly"]
    if settings["selection_role"] != "tune_only":
        raise ValueError("Persistence must be selected on tune only")
    scores = score_rows(bundle["artifact"], tune)
    candidates = []
    for consecutive in settings["consecutive_candidates"]:
        for cooldown in settings["cooldown_hours_candidates"]:
            result = metrics(persistent_alerts(scores, consecutive, cooldown), tune)
            if (
                result["false_alerts_per_1000_normal_hours"]
                <= settings["maximum_false_alerts_per_1000_normal_hours"]
            ):
                candidates.append(
                    dict(consecutive=consecutive, cooldown_hours=cooldown, tune=result)
                )
    if not candidates:
        raise ValueError("No persistence candidate meets validation false-alert budget")
    selected = min(
        candidates,
        key=lambda c: (
            -c["tune"]["event_recall"],
            c["tune"]["mean_detection_delay_hours"] or 0,
            c["tune"]["false_alerts"],
            c["consecutive"],
            c["cooldown_hours"],
        ),
    )
    frozen = dict(selected=selected, bundle_hash=bundle["bundle_hash"], selection_role="tune_only")
    frozen["policy_hash"] = content_hash(frozen)
    write_json(folder / "persistence.json", frozen)
    # Independent role is first scored only after policy has been saved.
    heldout = pd.concat(
        [fixture(cfg, "evaluation"), fixture(cfg, "evaluation", degrading=True)], ignore_index=True
    )
    if set(heldout.stream) & set(tune.stream):
        raise ValueError("Held-out stream leakage")
    alerts = persistent_alerts(
        score_rows(bundle["artifact"], heldout), selected["consecutive"], selected["cooldown_hours"]
    )
    robustness = {}
    normal = fixture(cfg, "evaluation", engines=1)
    for name, value in (
        ("missing", np.nan),
        ("impossible", 2000.0),
        ("plausible_extreme", 450.0),
        ("drift", 120.0),
    ):
        changed = normal.copy()
        changed.loc[changed.hour.between(20, 24), "temperature_c"] = value
        rows = score_rows(bundle["artifact"], changed)
        result = persistent_alerts(rows, selected["consecutive"], selected["cooldown_hours"])
        affected = [r for r in result if 20 <= r["hour"] <= 24]
        if name in {"missing", "impossible"} and any(r["state"] != "abstain" for r in affected):
            raise ValueError("Quality failures must abstain")
        robustness[name] = dict(
            affected_states=[r["state"] for r in affected],
            review_alerts=sum(r["review_alert"] for r in affected),
            diagnosis=None,
        )
    report = dict(
        policy=frozen,
        heldout=metrics(alerts, heldout),
        robustness=robustness,
        evaluation_groups=sorted(heldout.stream.unique()),
        scope="independent synthetic sensor-model fixtures; not operational aircraft evidence",
        alert_meaning="review_only",
        failure_probability=None,
    )
    write_json(ROOT / "docs/exports/anomaly_robustness.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=ROOT / "artifacts/anomaly")
    args = parser.parse_args()
    report = evaluate_anomaly(
        experiment(ROOT / "config/experiments.yaml"),
        args.model,
        yaml.safe_load((ROOT / "config/demo_policy.yaml").read_text()),
    )
    print(report["policy"]["selected"], report["heldout"], report["robustness"])


if __name__ == "__main__":
    main()
