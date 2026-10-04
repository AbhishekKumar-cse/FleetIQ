"""UTC ordering, explicit historical mode and a two-minute replay watermark."""

from datetime import UTC, datetime, timedelta


def utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Malformed timestamp") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(UTC)


def classify_time(
    observed_at,
    recorded_at,
    *,
    now,
    latest=None,
    mode="replay",
    future_skew_seconds=30,
    lateness_seconds=120,
):
    if mode not in {"replay", "historical"} or min(future_skew_seconds, lateness_seconds) < 0:
        raise ValueError("Invalid temporal policy")
    observed, recorded, now = utc(observed_at), utc(recorded_at), utc(now)
    if recorded < observed:
        return "invalid_recording_order"
    if max(observed, recorded) > now + timedelta(seconds=future_skew_seconds):
        return "future"
    if latest is not None and observed < utc(latest):
        if mode == "historical":
            return "historical_backfill"
        if observed < utc(latest) - timedelta(seconds=lateness_seconds):
            return "late_backfill_required"
        return "out_of_order"
    return "eligible"


class ReplayWatermark:
    def __init__(self):
        self.latest = {}

    def observe(self, stream, observed_at, recorded_at, *, now):
        classification = classify_time(
            observed_at, recorded_at, now=now, latest=self.latest.get(stream)
        )
        if classification in {"eligible", "out_of_order"}:
            observed = utc(observed_at)
            self.latest[stream] = max(observed, self.latest.get(stream, observed))
        return classification
