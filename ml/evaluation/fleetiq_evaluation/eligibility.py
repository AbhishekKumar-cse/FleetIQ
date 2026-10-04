"""Frozen task eligibility counts; no estimator fit or final-outcome access."""

import argparse
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yaml
from fleetiq_data.eda import training_group
from fleetiq_data.importer import UNIT_FIELDS
from fleetiq_data.quality.dedup import raw_json
from fleetiq_data.quality.profiles import load_profiles
from fleetiq_data.quality.time import utc
from fleetiq_data.quality.values import essential_supported, validate_value
from fleetiq_data.targets import synthetic_hour_target, training_cycle_targets

from fleetiq_evaluation.splits import (
    ROOT,
    benchmark_splits,
    content_hash,
    load_benchmark,
    load_config,
)
from fleetiq_evaluation.temporal_splits import CalendarSample, calendar_partitions, expanding_folds


def summarize(rows, *, minimum_history):
    rows = list(rows)
    if any(not row.eligible and row.label is not None for row in rows):
        raise ValueError("Censored/intervened samples cannot become negatives")
    eligible = [row for row in rows if row.eligible]
    if any(type(row.label) is not bool for row in eligible):
        raise ValueError("Eligible labels must be Boolean")
    positive = [row for row in eligible if row.label]
    negative = [row for row in eligible if not row.label]
    if any(not row.event_id for row in positive):
        raise ValueError("Positive windows require an independent event identity")
    return dict(
        samples=len(rows),
        eligible=len(eligible),
        positives=len(positive),
        negatives=len(negative),
        independent_events=len({row.event_id for row in positive}),
        independent_engines=len({row.installation_id for row in eligible}),
        positive_engines=len({row.installation_id for row in positive}),
        negative_engines=len({row.installation_id for row in negative}),
        aircraft=len({row.aircraft_id for row in eligible}),
        censored_or_ineligible=len(rows) - len(eligible),
        short_histories=sum(row.history_count < minimum_history for row in rows),
        reasons=dict(sorted(Counter(row.reason for row in rows if not row.eligible).items())),
    )


def calibration_supported(counts, task):
    return (
        counts["independent_events"] >= task["minimum_calibration_events"]
        and counts["positive_engines"] >= task["minimum_calibration_positive_engines"]
        and counts["negative_engines"] >= task["minimum_calibration_negative_engines"]
    )


def _observed(root, directory, filename, **kwargs):
    path = root / directory / filename
    if path.resolve() != path.absolute():
        raise ValueError("Observed inputs cannot redirect through symlinks")
    return pd.read_parquet(path, **kwargs)


def synthetic_samples(root, directory, cfg):
    calendar = cfg["calendar"]
    cutoff = utc(calendar["calibration_end"])
    horizon = calendar["horizon_hours"]
    lookback = timedelta(seconds=calendar["lookback_seconds"])
    frame = _observed(root, directory, "sensor_observations.parquet").drop_duplicates()
    if (
        set(frame.source_kind) != {"synthetic_engine"}
        or set(frame.schema_version) != {"synthetic-engine-v1"}
        or set(frame.life_unit) != {"operating_hours"}
    ):
        raise ValueError("Declared synthetic source schema/life unit required")
    if frame.duplicated(["installation_id", "operating_hours"]).any():
        raise ValueError("Conflicting source identities require receipt reconciliation")
    training_ids = sorted(
        frame.loc[frame.aircraft_id.map(training_group), "installation_id"].unique()
    )
    # Pushdown excludes held-out installations and confirmations beyond selection cutoff.
    faults = _observed(
        root,
        directory,
        "confirmed_faults.parquet",
        filters=[
            ("installation_id", "in", training_ids),
            ("recorded_at", "<=", cutoff.isoformat()),
        ],
    )
    if faults.installation_id.duplicated().any() or (faults.status != "confirmed").any():
        raise ValueError("Resolve conflicting/unconfirmed fault evidence")
    fault_by_install = {row["installation_id"]: row for row in faults.to_dict("records")}
    tasks = _observed(
        root, directory, "maintenance_tasks.parquet", columns=["task_id", "installation_id"]
    )
    task_install = dict(zip(tasks.task_id, tasks.installation_id, strict=True))
    if len(task_install) != len(tasks):
        raise ValueError("Conflicting task identities")
    maintenance = _observed(
        root,
        directory,
        "maintenance_events.parquet",
        filters=[("kind", "=", "work_started"), ("recorded_at", "<=", cutoff.isoformat())],
    )
    interventions = {}
    for record in maintenance.to_dict("records"):
        at, known = utc(record["event_time"]), utc(record["recorded_at"])
        if known < at:
            raise ValueError("Intervention recording precedes event")
        installation = task_install[record["task_id"]]
        if installation in training_ids:
            interventions.setdefault(installation, []).append(at)
    profiles = load_profiles("synthetic_engine")
    result, chronology_rejected = [], 0
    for installation, group in frame.groupby("installation_id", sort=True):
        observations = []
        for row in group.to_dict("records"):
            at, known = utc(row["measured_at"]), utc(row["recorded_at"])
            if known < at:
                chronology_rejected += 1
                continue
            values = {
                channel: validate_value(
                    None if pd.isna(row[channel]) else row[channel],
                    row[UNIT_FIELDS[channel]],
                    profile,
                    pressure_kind=row.get("pressure_kind", "absolute")
                    if profile.pressure_kind
                    else None,
                )
                for channel, profile in profiles.items()
            }
            observations.append(
                dict(row, at=at, known=known, quality_ok=essential_supported(values, profiles))
            )
        observations.sort(key=lambda row: row["at"])
        by_time = {row["at"]: row for row in observations}
        if len(by_time) != len(observations):
            raise ValueError("Conflicting installation timestamps")
        for row in observations:
            # Online prediction becomes possible at actual receipt, without rewriting measurement time.
            at = row["known"]
            end = at + timedelta(hours=horizon)
            history_count = sum(
                previous["quality_ok"] and previous["known"] <= at
                for previous in observations
                if at - lookback < previous["at"] <= at
            )
            arguments = dict(
                sample_id=f"synthetic:{installation}:{at.isoformat()}",
                installation_id=f"synthetic:engine-v1:{installation}",
                aircraft_id=row["aircraft_id"],
                as_of=at,
                window_start=at - lookback,
                recorded_at=row["known"],
                label_end=end,
                history_count=history_count,
            )
            if at >= cutoff or not training_group(row["aircraft_id"]):
                result.append(
                    CalendarSample(
                        **arguments,
                        label_recorded_at=None,
                        eligible=False,
                        label=None,
                        reason="held_out_labels_opaque",
                    )
                )
                continue
            fault = fault_by_install.get(installation)
            future_interventions = [
                value for value in interventions.get(installation, ()) if value > at
            ]
            intervention = min(future_interventions, default=None)
            # One additional hourly observation proves survival *past* the inclusive horizon.
            followup = [
                by_time.get(row["at"] + timedelta(hours=step)) for step in range(horizon + 2)
            ]
            complete = all(value is not None and value["quality_ok"] for value in followup)
            if complete:
                complete = all(
                    abs((value["operating_hours"] - row["operating_hours"]) - step) < 1e-6
                    for step, value in enumerate(followup)
                )
            target = synthetic_hour_target(
                as_of=at,
                label_cutoff=cutoff,
                event_time=utc(fault["event_time"]) if fault else None,
                event_recorded_at=utc(fault["recorded_at"]) if fault else None,
                followup_until=followup[-1]["at"] if complete else None,
                followup_recorded_at=max(value["known"] for value in followup)
                if complete
                else None,
                intervention_at=intervention,
                horizon_hours=horizon,
            )
            quality_ok = row["quality_ok"]
            available = (
                target.recorded_time
                if target.failure_within_horizon is True
                else max(value["known"] for value in followup)
                if target.eligible
                else None
            )
            result.append(
                CalendarSample(
                    **arguments,
                    label_recorded_at=available if quality_ok else None,
                    eligible=target.eligible and quality_ok,
                    label=target.failure_within_horizon if quality_ok else None,
                    event_id=f"synthetic:event:{fault['event_id']}"
                    if fault and target.failure_within_horizon
                    else None,
                    reason=target.reason if quality_ok else "essential_quality_unsupported",
                )
            )
    return result, dict(
        chronology_rejected=chronology_rejected,
        raw_rows=len(frame),
        observed_input_hash=content_hash(
            raw_json(frame.sort_values(["installation_id", "operating_hours"]).to_dict("records"))
        ),
        development_faults_hash=content_hash(faults.to_dict("records")),
        latent_truth_accessed=False,
        final_outcomes_accessed=False,
    )


def benchmark_report(frame, cfg, experiment, *, horizon_cycles=30):
    split = benchmark_splits(frame, cfg)
    labels = training_cycle_targets(frame, horizon_cycles=horizon_cycles)
    partitions = {}
    for role, identities in split["groups"].items():
        engines = {int(value.rsplit(":", 1)[1]) for value in identities}
        subset = labels[labels.unit_id.isin(engines)]
        eligible = subset[subset.eligible]
        positive = eligible[eligible.failure_within_horizon]
        negative = eligible[~eligible.failure_within_horizon]
        partitions[role] = dict(
            samples=len(subset),
            eligible=len(eligible),
            positives=len(positive),
            negatives=len(negative),
            independent_events=positive.unit_id.nunique(),
            independent_engines=eligible.unit_id.nunique(),
            positive_engines=positive.unit_id.nunique(),
            negative_engines=negative.unit_id.nunique(),
            censored_or_ineligible=len(subset) - len(eligible),
            short_histories=int((subset.cycle < experiment["benchmark_history_minimum"]).sum()),
        )
    calibration = partitions["calibration"]
    support = calibration_supported(calibration, experiment["tasks"]["failure_risk"])
    return dict(
        track="cmapss_benchmark",
        life_unit="cycles",
        split=split,
        partitions=partitions,
        calibration_supported=support,
        risk_training_supported=partitions["fit"]["independent_engines"]
        >= experiment["tasks"]["failure_risk"]["minimum_fit_engines"],
        rul_training_supported=partitions["fit"]["independent_engines"]
        >= experiment["tasks"]["rul"]["minimum_fit_engines"],
        rul_interval_supported=calibration["independent_engines"]
        >= experiment["tasks"]["rul"]["minimum_calibration_engines"],
        anomaly_training_supported=partitions["fit"]["independent_engines"]
        >= experiment["tasks"]["anomaly"]["minimum_fit_engines"],
        probability_deployment_allowed=False,
        official_test_outcomes_accessed=False,
    )


def calendar_report(rows, manifest, experiment):
    by_id = {row.sample_id: row for row in rows}
    partitions = {
        role: summarize(
            (by_id[identity] for identity in manifest["groups"][role]),
            minimum_history=experiment["synthetic_history_minimum"],
        )
        for role in ("fit", "tune", "calibration")
    }
    calibration = partitions["calibration"]
    support = calibration_supported(calibration, experiment["tasks"]["failure_risk"])
    fit = partitions["fit"]
    # Label evidence alone cannot enable training with an entirely short feature profile.
    risk_training = (
        fit["independent_engines"] >= experiment["tasks"]["failure_risk"]["minimum_fit_engines"]
        and fit["short_histories"] < fit["samples"]
    )
    boundaries = [utc(value) for value in manifest["boundaries"]]
    phase_exclusions = {}
    for index, role in enumerate(("fit", "tune", "calibration")):
        phase_exclusions[role] = dict(
            sorted(
                Counter(
                    manifest["excluded"][row.sample_id]
                    for row in rows
                    if boundaries[index] <= row.as_of < boundaries[index + 1]
                    and row.sample_id in manifest["excluded"]
                ).items()
            )
        )
    return dict(
        track="synthetic_engine_demo",
        life_unit="operating_hours",
        fold=manifest["version"],
        partitions=partitions,
        calibration_supported=support,
        risk_training_supported=risk_training,
        anomaly_training_supported=fit["independent_engines"]
        >= experiment["tasks"]["anomaly"]["minimum_fit_engines"],
        rul_training_supported=False,
        probability_deployment_allowed=False,
        exclusions=dict(sorted(Counter(manifest["excluded"].values()).items())),
        phase_exclusions=phase_exclusions,
        censored_before_selection=sum(
            row.as_of < boundaries[3]
            and not row.eligible
            and row.reason != "held_out_labels_opaque"
            for row in rows
        ),
        final_inventory={
            role: dict(samples=len(manifest["groups"][role]), outcomes_accessed=False)
            for role in ("known_asset_future", "new_asset_future", "held_out_installation")
        },
        split_hash=manifest["manifest_hash"],
        limitations=[
            "synthetic_rul_censored_or_intervened",
            "short_hourly_feature_windows",
            "known_asset_future_exploratory_after_training_aircraft_eda",
        ],
    )


def build_report(experiment, *, root=ROOT):
    if experiment["tasks"]["failure_risk"]["probability_deployment_enabled"]:
        raise ValueError(
            "Probability deployment requires later independent model/calibration review; Step 050 only counts evidence"
        )
    if experiment["sources"] != {
        "benchmark": "data/processed/cmapss/train.parquet",
        "synthetic": "data/synthetic/observed",
    }:
        raise ValueError(
            "Only approved training/observed sources are accessible; official test/evaluator forbidden"
        )
    if (
        experiment["splits"] != "config/splits.yaml"
        or experiment["targets"] != "config/targets.json"
    ):
        raise ValueError("Approved frozen split/target contracts required")
    cfg = load_config(root / experiment["splits"])
    targets = json.loads((root / experiment["targets"]).read_text())
    if cfg["calendar"]["horizon_hours"] != targets["synthetic"]["horizon_hours"]:
        raise ValueError("Target/split horizon mismatch")
    benchmark = benchmark_report(
        load_benchmark(root), cfg, experiment, horizon_cycles=targets["cmapss"]["horizon_cycles"]
    )
    samples, provenance = synthetic_samples(root, experiment["sources"]["synthetic"], cfg)
    primary = calendar_partitions(samples, cfg)
    folds = expanding_folds(samples, cfg)
    reports = [calendar_report(samples, manifest, experiment) for manifest in [primary, *folds]]
    result = dict(
        version=experiment["version"],
        experiments_hash=content_hash(experiment),
        targets_hash=content_hash(targets),
        declared_tasks=experiment["tasks"],
        declared_roles=experiment["roles"],
        benchmark=benchmark,
        synthetic=reports,
        synthetic_provenance=provenance,
        probability_deployment_allowed=False,
        model_training_performed=False,
        final_outcomes_accessed=False,
        stop_conditions=[
            "risk_probability_and_rul_intervals_require_more_independent_calibration_engines",
            "synthetic_hourly_temporal_profile_requires_review_before_risk_training",
        ],
    )
    return result | {"report_hash": content_hash(result)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "docs/exports").resolve()):
        raise ValueError("Eligibility evidence belongs under ignored docs/exports")
    report = build_report(yaml.safe_load(args.config.read_text()))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    print(
        json.dumps(
            dict(
                report_hash=report["report_hash"],
                probability_deployment_allowed=False,
                calibration_engines=report["benchmark"]["partitions"]["calibration"][
                    "independent_engines"
                ],
            )
        )
    )


if __name__ == "__main__":
    main()
