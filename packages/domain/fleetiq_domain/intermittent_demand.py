"""Syntetos–Boylan adjusted Croston reference for equal-width consumption bins."""

import math


def sba_croston(history, *, alpha=0.1, minimum_bins=12, minimum_events=4):
    values = tuple(float(x) for x in history)
    if (
        not 0 < alpha < 1
        or minimum_bins < 1
        or minimum_events < 2
        or any(not math.isfinite(x) or x < 0 for x in values)
    ):
        raise ValueError("Nonnegative finite consumption and valid support policy required")
    indices = [i for i, x in enumerate(values) if x > 0]
    if len(values) < minimum_bins or len(indices) < minimum_events:
        return {
            "supported": False,
            "rate_per_bin": None,
            "reason": "insufficient independent demand events/history",
            "events": len(indices),
            "bins": len(values),
        }
    first = indices[0]
    size, interval = values[first], float(first + 1)
    previous = first
    for i in indices[1:]:
        size = alpha * values[i] + (1 - alpha) * size
        interval = alpha * (i - previous) + (1 - alpha) * interval
        previous = i
    return {
        "supported": True,
        "rate_per_bin": (1 - alpha / 2) * size / interval,
        "alpha": alpha,
        "events": len(indices),
        "bins": len(values),
        "assumptions": "equal-width bins; consumption ledger, not reservations; stationary intermittent demand",
    }
