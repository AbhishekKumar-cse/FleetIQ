from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_data.eda import training_group
from fleetiq_evaluation.splits import load_config
from fleetiq_evaluation.temporal_splits import CalendarSample, calendar_partitions, expanding_folds


def sample(name, day, *, aircraft=None):
    group = aircraft or next(str(i) for i in range(100) if training_group(str(i)))
    at = datetime(2026, 1, day, 12, tzinfo=UTC)
    return CalendarSample(
        name,
        "installation",
        group,
        at,
        at - timedelta(hours=1),
        at,
        at + timedelta(hours=24),
        at + timedelta(hours=24),
        True,
        False,
    )


def test_ordered_purged_known_and_heldout_future_roles():
    other = next(str(i) for i in range(100) if not training_group(str(i)))
    rows = [
        sample("fit", 2),
        sample("tune", 6),
        sample("calibration", 9),
        sample("future", 12),
        replace(sample("heldout", 12, aircraft=other), installation_id="other"),
        replace(sample("new", 12), installation_id="new-installation"),
    ]
    manifest = calendar_partitions(rows)
    assert manifest["groups"]["fit"] == ["fit"]
    assert manifest["groups"]["tune"] == ["tune"]
    assert manifest["groups"]["calibration"] == ["calibration"]
    assert manifest["groups"]["known_asset_future"] == ["future"]
    assert manifest["groups"]["new_asset_future"] == ["new"]
    assert manifest["groups"]["held_out_installation"] == ["heldout"]
    assert manifest["final_future_labels_accessed"] is False
    assert manifest == calendar_partitions(list(reversed(rows)))
    poisoned = [
        replace(row, label=True, event_id="future-outcome", label_recorded_at=row.as_of)
        if row.sample_id in {"future", "new", "heldout"}
        else row
        for row in rows
    ]
    assert calendar_partitions(poisoned) == manifest


def test_immature_late_feature_horizon_and_embargo_are_excluded():
    rows = [
        replace(sample("late-label", 2), label_recorded_at=datetime(2026, 1, 6, tzinfo=UTC)),
        replace(sample("late-feature", 2), recorded_at=datetime(2026, 1, 3, tzinfo=UTC)),
        sample("crossing", 4),
        replace(
            sample("censored", 2),
            eligible=False,
            label=None,
            label_recorded_at=None,
            reason="censored",
        ),
    ]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows.append(
        replace(
            sample("embargo", 1),
            as_of=start,
            window_start=start - timedelta(hours=1),
            recorded_at=start,
            label_end=start + timedelta(hours=24),
        )
    )
    manifest = calendar_partitions(rows)
    assert not manifest["groups"]["fit"]
    assert set(manifest["excluded"].values()) == {
        "label_unavailable_at_cutoff",
        "feature_unavailable_at_as_of",
        "horizon_crosses_phase",
        "censored",
        "lookback_embargo",
    }


def test_expanding_folds_independently_enforce_availability_and_native_clock():
    rows = [sample("a", 2), sample("b", 6), sample("c", 9), sample("d", 12)]
    folds = expanding_folds(rows)
    by_id = {row.sample_id: row for row in rows}
    for fold in folds:
        cutoff = datetime.fromisoformat(fold["boundaries"][1])
        for identity in fold["groups"]["fit"]:
            row = by_id[identity]
            assert row.recorded_at <= row.as_of
            assert row.label_end <= cutoff and row.label_recorded_at <= cutoff
    assert set(folds[0]["groups"]["fit"]).issubset(folds[1]["groups"]["fit"])
    with pytest.raises(ValueError, match="NASA"):
        calendar_partitions(rows, track="cmapss_benchmark")
    with pytest.raises(ValueError, match="Calendar"):
        calendar_partitions([replace(rows[0], as_of=2)])
    cfg = load_config()
    cfg["calendar"]["embargo_seconds"] = 0
    with pytest.raises(ValueError, match="embargo"):
        calendar_partitions(rows, cfg)
