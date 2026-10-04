"""Population statistics over canonical, observed, causally eligible samples."""

import math
from statistics import median

from fleetiq_features.schema import (
    CONFIG,
    STATISTICS,
    eligible,
    masks,
    select_window,
    track_schema,
    validate_channels,
)


def statistics(values):
    values = list(values)
    count = len(values)
    if not count:
        return dict.fromkeys(STATISTICS, None) | {"count": 0}
    # Scaling preserves finite results for large same-sign means/RMS without x*x overflow.
    scale = max(abs(value) for value in values)
    normalized = [value / scale for value in values] if scale else values
    mean_normalized = math.fsum(normalized) / count
    mean = mean_normalized * scale
    variance_normalized = math.fsum((value - mean_normalized) ** 2 for value in normalized) / count
    std = math.sqrt(variance_normalized) * scale
    variance = std * std
    rms = math.sqrt(math.fsum(value * value for value in normalized) / count) * scale
    result = dict(
        count=count,
        mean=mean,
        variance=variance,
        std=std,
        median=median(normalized) * scale,
        min=min(values),
        max=max(values),
        rms=rms,
    )
    return {
        key: value if value is None or math.isfinite(value) else None
        for key, value in result.items()
    }


def basic_features(channels, *, track, as_of, window_start, source_cutoff=None):
    validate_channels(channels, track)
    vector, channel_masks, reasons = {}, {}, {}
    for channel in track_schema(track)["channels"]:
        rows = select_window(
            channels.get(channel, ()),
            track=track,
            as_of=as_of,
            window_start=window_start,
            source_cutoff=source_cutoff,
        )
        result = statistics(float(row.value) for row in rows if eligible(row))
        vector.update({f"{channel}.{name}": result[name] for name in STATISTICS})
        channel_masks[channel] = masks(rows)
        if result["count"] == 0:
            reasons[channel] = "no_observed_eligible_samples"
        elif any(result[name] is None for name in STATISTICS):
            reasons[channel] = "numeric_range_unsupported"
    return {
        "version": CONFIG["version"],
        "track": track,
        "features": vector,
        "masks": channel_masks,
        "supported": not reasons,
        "reasons": reasons,
    }
