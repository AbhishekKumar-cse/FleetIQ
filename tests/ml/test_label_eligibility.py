from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
import yaml
from fleetiq_data.eda import training_group
from fleetiq_data.targets import synthetic_hour_target
from fleetiq_evaluation.eligibility import (
    build_report,
    calendar_report,
    calibration_supported,
    summarize,
)
from fleetiq_evaluation.splits import ROOT
from fleetiq_evaluation.temporal_splits import CalendarSample, calendar_partitions


def test_censored_intervened_late_confirmed_never_become_negative():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    base = dict(
        as_of=at,
        label_cutoff=at + timedelta(days=2),
        followup_until=None,
        followup_recorded_at=None,
    )
    censored = synthetic_hour_target(**base)
    intervened = synthetic_hour_target(
        **(
            base
            | dict(
                followup_until=at + timedelta(days=1),
                followup_recorded_at=at + timedelta(days=1),
                intervention_at=at + timedelta(hours=12),
            )
        )
    )
    late = synthetic_hour_target(
        **(
            base
            | dict(event_time=at + timedelta(hours=5), event_recorded_at=at + timedelta(days=3))
        )
    )
    for target in (censored, intervened, late):
        assert not target.eligible and target.failure_within_horizon is None


def test_independent_event_counts_do_not_inflate_overlapping_windows():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    row = CalendarSample(
        "a",
        "engine",
        "aircraft",
        at,
        at - timedelta(hours=1),
        at,
        at + timedelta(hours=24),
        at + timedelta(hours=12),
        True,
        True,
        "same-failure",
        1,
    )
    rows = [replace(row, sample_id=str(i)) for i in range(100)]
    counts = summarize(rows, minimum_history=3)
    assert counts["positives"] == 100 and counts["independent_events"] == 1
    assert counts["positive_engines"] == 1 and counts["short_histories"] == 100
    assert not calibration_supported(
        counts,
        dict(
            minimum_calibration_events=20,
            minimum_calibration_positive_engines=20,
            minimum_calibration_negative_engines=20,
        ),
    )
    with pytest.raises(ValueError, match="Censored"):
        summarize([replace(row, eligible=False, label=False)], minimum_history=3)
    with pytest.raises(ValueError, match="event"):
        summarize([replace(row, event_id=None)], minimum_history=3)


def test_report_receipt_cutoffs_disjoint_calibration_and_inaccessible_final_faults(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    for filename in ("splits.yaml", "targets.json"):
        (config / filename).write_text((ROOT / "config" / filename).read_text())
    experiment = yaml.safe_load((ROOT / "config/experiments.yaml").read_text())
    benchmark = tmp_path / "data/processed/cmapss"
    benchmark.mkdir(parents=True)
    pd.DataFrame(
        [
            dict(
                unit_id=unit,
                cycle=cycle,
                **{f"setting_{i}": 0 for i in range(1, 4)},
                **{f"sensor_{i}": 7 for i in range(1, 22)},
            )
            for unit in range(1, 101)
            for cycle in (1, 2)
        ]
    ).to_parquet(benchmark / "train.parquet")
    observed = tmp_path / "data/synthetic/observed"
    observed.mkdir(parents=True)
    aircraft = next(str(i) for i in range(100) if training_group(str(i)))
    heldout = next(str(i) for i in range(100) if not training_group(str(i)))
    at = datetime(2026, 1, 1, tzinfo=UTC)
    records = []
    for installation, group in (("training", aircraft), ("heldout", heldout)):
        for step in range(61):
            time = at + timedelta(hours=step)
            records.append(
                dict(
                    installation_id=installation,
                    aircraft_id=group,
                    source_kind="synthetic_engine",
                    schema_version="synthetic-engine-v1",
                    life_unit="operating_hours",
                    measured_at=time.isoformat(),
                    recorded_at=(time + timedelta(seconds=5)).isoformat(),
                    operating_hours=step,
                    temperature_c=100,
                    temperature_unit="degC",
                    oil_pressure_kpa=300,
                    pressure_unit="kPa",
                    vibration_mm_s=2,
                    vibration_unit="mm/s",
                )
            )
    pd.DataFrame(records).to_parquet(observed / "sensor_observations.parquet")
    # Poisoned event times fail parsing if forbidden final/heldout label rows are ever read.
    pd.DataFrame(
        [
            dict(
                installation_id="training",
                event_id="future",
                status="confirmed",
                event_time="FORBIDDEN_FINAL_OUTCOME",
                recorded_at="2026-01-12T00:00:00+00:00",
            ),
            dict(
                installation_id="heldout",
                event_id="heldout",
                status="confirmed",
                event_time="FORBIDDEN_HELDOUT_OUTCOME",
                recorded_at="2026-01-02T00:00:00+00:00",
            ),
        ]
    ).to_parquet(observed / "confirmed_faults.parquet")
    pd.DataFrame(columns=["task_id", "installation_id"]).to_parquet(
        observed / "maintenance_tasks.parquet"
    )
    pd.DataFrame(
        {name: pd.Series(dtype="str") for name in ["kind", "recorded_at", "event_time", "task_id"]}
    ).to_parquet(observed / "maintenance_events.parquet")
    first = build_report(experiment, root=tmp_path)
    assert first == build_report(experiment, root=tmp_path)
    assert first["synthetic"][0]["partitions"]["fit"]["negatives"] > 0
    assert (
        first["probability_deployment_allowed"] is False
        and first["final_outcomes_accessed"] is False
    )
    groups = first["benchmark"]["split"]["groups"]
    assert not set(groups["calibration"]).intersection(groups["fit"] + groups["tune"])
    changed = deepcopy(experiment)
    changed["tasks"]["failure_risk"]["probability_deployment_enabled"] = True
    with pytest.raises(ValueError, match="Probability"):
        build_report(changed, root=tmp_path)


def test_final_outcomes_cannot_change_selection_report():
    experiment = yaml.safe_load((ROOT / "config/experiments.yaml").read_text())
    aircraft = next(str(i) for i in range(100) if training_group(str(i)))
    at = datetime(2026, 1, 2, tzinfo=UTC)
    first = CalendarSample(
        "fit",
        "engine",
        aircraft,
        at,
        at - timedelta(hours=1),
        at,
        at + timedelta(hours=24),
        at + timedelta(hours=24),
        True,
        False,
    )
    future_at = datetime(2026, 1, 12, tzinfo=UTC)
    future = replace(
        first,
        sample_id="future",
        as_of=future_at,
        window_start=future_at - timedelta(hours=1),
        recorded_at=future_at,
        label_end=future_at + timedelta(hours=24),
    )
    rows = [first, future]
    manifest = calendar_partitions(rows)
    before = calendar_report(rows, manifest, experiment)
    poisoned = [
        first,
        replace(
            future, eligible=False, label=True, event_id="unseen-final-event", reason="censored"
        ),
    ]
    assert calendar_partitions(poisoned) == manifest
    assert calendar_report(poisoned, manifest, experiment) == before
    changed = deepcopy(experiment)
    changed["sources"]["benchmark"] = "data/raw/cmapss/RUL_FD001.txt"
    with pytest.raises(ValueError, match="official test"):
        build_report(changed)
