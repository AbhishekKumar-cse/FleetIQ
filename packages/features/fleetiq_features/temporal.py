"""Trailing temporal features, native elapsed clocks and frozen train-only baselines."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import timedelta
from numbers import Real

from fleetiq_features.basic import statistics
from fleetiq_features.schema import (
    ROOT,
    clock,
    eligible,
    masks,
    select_window,
    track_schema,
    validate_channels,
)

CONFIG = json.loads((ROOT / "config/features_v2.json").read_text())


@dataclass(frozen=True)
class TrainingBaseline:
    mean: float
    channel: str
    track: str
    version: str
    content_hash: str
    training_groups: tuple[str, ...]
    partition: str = "train"

    def __post_init__(self):
        if (
            self.partition != "train"
            or not isinstance(self.mean, Real)
            or not math.isfinite(self.mean)
            or not self.version
            or not self.training_groups
            or len(self.content_hash) != 64
        ):
            raise ValueError("Finite frozen train-only baseline provenance required")
        if self.channel not in track_schema(self.track)["channels"]:
            raise ValueError("Baseline channel/track mismatch")


def fit_baseline(values, *, channel, track, partition, training_groups, version):
    """Caller must supply explicit training membership; extraction never refits on inputs."""
    if partition != "train" or not training_groups:
        raise ValueError("Only an explicitly identified training partition may fit baselines")
    values = list(values)
    if not values or any(
        not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("Finite observed training values required")
    values = [float(value) for value in values]
    groups = tuple(sorted(set(map(str, training_groups))))
    body = {
        "channel": channel,
        "track": track,
        "partition": partition,
        "groups": groups,
        "version": version,
        "values": values,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return TrainingBaseline(statistics(values)["mean"], channel, track, version, digest, groups)


def _elapsed(later, earlier):
    difference = later - earlier
    return difference.total_seconds() if isinstance(difference, timedelta) else difference


def temporal_statistics(rows, *, track, alpha=0.3, baseline=None):
    track_schema(track)
    rows = list(rows)
    timestamps = [clock(row.at, track) for row in rows]
    if timestamps != sorted(set(timestamps)) or len({row.stream for row in rows}) > 1:
        raise ValueError("Canonical ordered single-stream samples required")
    if isinstance(alpha, bool) or not isinstance(alpha, Real) or not 0 < alpha <= 1:
        raise ValueError("EWMA alpha must be in (0,1]")
    observed = [row for row in rows if eligible(row)]
    values = [float(row.value) for row in observed]
    result = dict.fromkeys(CONFIG["statistics"], None)
    if not values:
        return result
    base = statistics(values)
    result.update(rolling_mean=base["mean"], rolling_std=base["std"])
    smoothed = values[0]
    for value in values[1:]:
        smoothed = (1 - alpha) * smoothed + alpha * value
    result["ewma"] = smoothed
    if baseline is not None:
        if not isinstance(baseline, TrainingBaseline) or baseline.track != track:
            raise ValueError("Track-compatible frozen training baseline required")
        result["residual"] = values[-1] - baseline.mean
    if len(values) >= 2:
        # Missing/invalid/imputed points break adjacent lag/delta chains; no bridging gaps.
        if len(rows) >= 2 and eligible(rows[-1]) and eligible(rows[-2]):
            elapsed = _elapsed(clock(rows[-1].at, track), clock(rows[-2].at, track))
            if elapsed > 0:
                result.update(
                    lag=float(rows[-2].value), delta=float(rows[-1].value) - float(rows[-2].value)
                )
                result["derivative"] = result["delta"] / elapsed
        xs = [_elapsed(clock(row.at, track), clock(observed[0].at, track)) for row in observed]
        scale_x = max(xs)
        scale_y = max(map(abs, values))
        normalized_x = [x / scale_x for x in xs] if scale_x else xs
        normalized_y = [y / scale_y for y in values] if scale_y else values
        mx = math.fsum(normalized_x) / len(xs)
        my = math.fsum(normalized_y) / len(values)
        xx = math.fsum((x - mx) ** 2 for x in normalized_x)
        if xx > 0:
            covariance = math.fsum(
                (x - mx) * (y - my) for x, y in zip(normalized_x, normalized_y, strict=True)
            )
            result["slope"] = (covariance / xx) * (scale_y / scale_x)
    # Declared biased population standardized moments; constants remain undefined.
    scale = max(map(abs, values))
    normalized = [value / scale for value in values] if scale else values
    mean = math.fsum(normalized) / len(normalized)
    centered = [value - mean for value in normalized]
    m2 = math.fsum(value**2 for value in centered) / len(values)
    if m2 > 0 and len(values) >= CONFIG["minimum_samples"]["skew"]:
        result["skew"] = (math.fsum(value**3 for value in centered) / len(values)) / (m2**1.5)
    if m2 > 0 and len(values) >= CONFIG["minimum_samples"]["kurtosis_excess"]:
        result["kurtosis_excess"] = (math.fsum(value**4 for value in centered) / len(values)) / (
            m2 * m2
        ) - 3
    return {
        key: value if value is None or math.isfinite(value) else None
        for key, value in result.items()
    }


def temporal_feature_names(track):
    track_schema(track)
    return tuple(
        f"{channel}.w{window}.{stat}"
        for channel in track_schema(track)["channels"]
        for window in CONFIG["tracks"][track]["windows"]
        for stat in CONFIG["statistics"]
    )


def temporal_features(channels, *, track, as_of, source_cutoff=None, baselines=None, alpha=None):
    validate_channels(channels, track)
    end = clock(as_of, track)
    baselines = baselines or {}
    validate_channels(baselines, track)
    vector, channel_masks, reasons = {}, {}, {}
    for channel in track_schema(track)["channels"]:
        baseline = baselines.get(channel)
        if baseline is not None and (
            not isinstance(baseline, TrainingBaseline)
            or baseline.channel != channel
            or baseline.track != track
        ):
            raise ValueError("Frozen baseline channel/track mismatch")
        for window in CONFIG["tracks"][track]["windows"]:
            start = end - window if track == "cmapss_benchmark" else end - timedelta(seconds=window)
            rows = select_window(
                channels.get(channel, ()),
                track=track,
                as_of=end,
                window_start=start,
                source_cutoff=source_cutoff,
            )
            result = temporal_statistics(
                rows,
                track=track,
                alpha=CONFIG["ewma_alpha"] if alpha is None else alpha,
                baseline=baseline,
            )
            prefix = f"{channel}.w{window}"
            vector.update({f"{prefix}.{key}": value for key, value in result.items()})
            channel_masks[prefix] = masks(rows)
            missing = [key for key, value in result.items() if value is None]
            if missing:
                reasons[prefix] = {
                    "unsupported_features": missing,
                    "observed_count": sum(eligible(row) for row in rows),
                    "baseline": "missing" if baseline is None else baseline.content_hash,
                }
    return {
        "version": CONFIG["version"],
        "track": track,
        "time_unit": CONFIG["tracks"][track]["time_unit"],
        "features": vector,
        "masks": channel_masks,
        "supported": not reasons,
        "reasons": reasons,
    }
