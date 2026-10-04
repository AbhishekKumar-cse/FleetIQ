from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_data.quality.dedup import DuplicateConflict, content_hash, remember
from fleetiq_data.quality.time import ReplayWatermark, classify_time, utc
from fleetiq_data.quality.windows import coverage, transition_flags

pytestmark = pytest.mark.unit
T = datetime(2026, 1, 1, tzinfo=UTC)


def test_timestamp_boundaries():
    assert utc("2026-01-01T05:30:00+05:30") == T
    for t in ["2026-01-01T00:00:00", "bad", T.replace(tzinfo=None)]:
        with pytest.raises(ValueError):
            utc(t)
    assert classify_time(T, T + timedelta(seconds=30), now=T) == "eligible"
    assert classify_time(T, T + timedelta(seconds=31), now=T) == "future"
    assert classify_time(T, T - timedelta(seconds=1), now=T) == "invalid_recording_order"
    latest = T + timedelta(minutes=2)
    assert classify_time(T, T, now=latest, latest=latest) == "out_of_order"
    assert (
        classify_time(T - timedelta(microseconds=1), T, now=latest, latest=latest)
        == "late_backfill_required"
    )
    assert (
        classify_time(T, T, now=latest, latest=latest, mode="historical") == "historical_backfill"
    )


def test_watermark_rejection_does_not_poison_stream():
    w = ReplayWatermark()
    assert w.observe("a", T, T, now=T) == "eligible"
    assert w.observe("a", T + timedelta(days=1), T + timedelta(days=1), now=T) == "future"
    assert w.latest["a"] == T
    assert w.observe("b", T - timedelta(days=1), T, now=T) == "eligible"


def test_duplicate_hash_includes_changed_time_and_value():
    receipts = {}
    row = {"at": T, "value": 1}
    assert remember(receipts, "event", row)
    assert not remember(receipts, "event", dict(reversed(list(row.items()))))
    for changed in [{"at": T, "value": 2}, {"at": T + timedelta(seconds=1), "value": 1}]:
        with pytest.raises(DuplicateConflict):
            remember(receipts, "event", changed)
    assert content_hash({"value": float("nan")}) == content_hash({"value": float("nan")})


def test_duration_coverage_and_future_invariance():
    samples = [(T, 1), (T + timedelta(seconds=10), 2), (T + timedelta(seconds=20), 3)]
    kwargs = dict(start=T, as_of=T + timedelta(seconds=30), cadence_seconds=10, max_gap_seconds=5)
    result = coverage(samples, **kwargs)
    assert result.coverage == 1 and result.supported
    assert coverage(samples + [(T + timedelta(seconds=31), 900)], **kwargs) == result
    partial = coverage(samples[:1], **kwargs)
    assert partial.coverage == pytest.approx(1 / 3) and partial.max_gap_seconds == 20
    assert not partial.supported
    assert coverage([], **kwargs).max_gap_seconds == 30
    irregular = coverage([(T, 1), (T + timedelta(seconds=12), 2)], **kwargs)
    assert irregular.observed_seconds == 20 and irregular.max_gap_seconds == 8
    missing = coverage([(T, 1), (T + timedelta(seconds=5), None)], **kwargs)
    assert missing.observed_seconds == 5
    assert coverage([(T - timedelta(seconds=100), 1)], **kwargs).coverage == 0


def test_usage_rollback_and_retained_spike():
    assert transition_flags(1, 30, 10, 9, spike_delta=10) == ("usage_rollback", "sensor_spike")
    assert transition_flags(1, 11, 9, 10, spike_delta=10) == ()
