"""Versioned support indicators; no aircraft survival probability or status mutation."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class HealthPolicy:
    version: str = "support-health-v1"
    watch_risk: float = 0.3
    critical_risk: float = 0.8
    maximum_age: timedelta = timedelta(hours=6)

    def __post_init__(self):
        if (
            not self.version
            or not 0 <= self.watch_risk < self.critical_risk <= 1
            or self.maximum_age <= timedelta(0)
        ):
            raise ValueError("Invalid versioned health policy")


@dataclass(frozen=True)
class ComponentEvidence:
    component_id: str
    track: str
    horizon: float
    unit: str
    as_of: datetime
    bundle_hash: str
    coverage: str
    essential_observed: bool
    calibrated_probability: float | None = None
    probability_display_enabled: bool = False
    ood: bool = False
    persistent_anomaly: bool = False
    confirmed_constraint: bool = False
    urgency: str | None = None


def component_health(
    evidence, *, now, expected_bundle, track, horizon, unit, policy=HealthPolicy()
):
    reasons = []
    stale = False
    if any(d.tzinfo is None or d.utcoffset() is None for d in (now, evidence.as_of)):
        raise ValueError("Aware evidence clocks required")
    if evidence.bundle_hash != expected_bundle:
        stale = True
        reasons.append("stale_bundle")
    if (
        evidence.as_of > now
        or now - evidence.as_of > policy.maximum_age
        or evidence.coverage == "stale"
    ):
        stale = True
        reasons.append("stale_or_future_evidence")
    if (evidence.track, evidence.horizon, evidence.unit) != (track, horizon, unit):
        reasons.append("incompatible_prediction_signature")
    if (
        evidence.coverage not in {"full", "partial"}
        or not evidence.essential_observed
        or evidence.ood
    ):
        reasons.append("essential_coverage_or_distribution_unsupported")
    p = evidence.calibrated_probability
    if (
        not evidence.probability_display_enabled
        or p is None
        or not math.isfinite(p)
        or not 0 <= p <= 1
    ):
        reasons.append("calibrated_probability_unavailable")
    common = dict(
        component_id=evidence.component_id,
        policy_version=policy.version,
        horizon=horizon,
        unit=unit,
        track=track,
        meaning="support_indicator_not_survival_probability",
    )
    if reasons:
        return common | dict(
            score=None,
            category="unknown",
            coverage="stale" if stale else "unsupported",
            reasons=reasons,
        )
    category = "healthy"
    if (
        p >= policy.critical_risk
        or evidence.confirmed_constraint
        or evidence.urgency in {"immediate", "urgent"}
    ):
        category = "critical"
    elif p >= policy.watch_risk or evidence.persistent_anomaly:
        category = "watch"
    return common | dict(
        score=100 * (1 - p),
        category=category,
        coverage=evidence.coverage,
        reasons=["minimum_eligible_component_risk_rule"],
    )


def aircraft_health(
    evidence,
    *,
    essential_components,
    critical_components,
    now,
    expected_bundle,
    track,
    horizon,
    unit,
    policy=HealthPolicy(),
):
    evidence = list(evidence)
    if len({e.component_id for e in evidence}) != len(evidence):
        raise ValueError("Select one versioned prediction per component")
    rows = {
        e.component_id: component_health(
            e,
            now=now,
            expected_bundle=expected_bundle,
            track=track,
            horizon=horizon,
            unit=unit,
            policy=policy,
        )
        for e in evidence
    }
    essential, critical = set(essential_components), set(critical_components)
    missing = sorted(c for c in essential if c not in rows or rows[c]["score"] is None)
    eligible = [rows[c] for c in sorted(critical) if c in rows and rows[c]["score"] is not None]
    common = dict(
        policy_version=policy.version,
        track=track,
        horizon=horizon,
        unit=unit,
        components=list(rows.values()),
        meaning="support_indicator_not_survival_probability",
    )
    if missing or not eligible:
        return common | dict(
            score=None,
            category="unknown",
            coverage="stale"
            if any(r["coverage"] == "stale" for r in rows.values())
            else "unsupported",
            limiting_component_id=None,
            reasons=["missing_essential_coverage" if missing else "no_eligible_critical_component"],
            missing=missing,
        )
    limiting = min(eligible, key=lambda r: (r["score"], r["component_id"]))
    rank = {"healthy": 0, "watch": 1, "critical": 2}
    category = max((r["category"] for r in eligible), key=rank.get)
    partial = any(c not in rows or rows[c]["coverage"] != "full" for c in critical | essential)
    return common | dict(
        score=limiting["score"],
        category=category,
        coverage="partial" if partial else "full",
        limiting_component_id=limiting["component_id"],
        reasons=["minimum_eligible_critical_component"],
        missing=[],
    )
