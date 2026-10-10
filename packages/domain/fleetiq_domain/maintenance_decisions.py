"""Deterministic engineering review, with compatible-unit resource margins only."""

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DecisionEvidence:
    evidence_ids: tuple[str, ...]
    model_version: str
    feature_version: str
    coverage: str = "unsupported"
    ood: bool = False
    confirmed_constraint: bool = False
    deadline: datetime | None = None
    calibrated_risk: float | None = None
    probability_display_enabled: bool = False
    persistent_anomaly: bool = False
    lower_rul: float | None = None
    interval_supported: bool = False
    rul_unit: str | None = None
    track: str = "synthetic_sensor_model"
    operating_hours_per_calendar_hour: float | None = None
    usage_mapping_version: str | None = None
    resource_lead_hours: float = 0


def prioritize(
    evidence,
    *,
    now,
    templates,
    policy_version="maintenance-review-v1",
    high_risk=0.8,
    deadline_warning_hours=24,
):
    if (
        not evidence.evidence_ids
        or not evidence.model_version
        or not evidence.feature_version
        or not policy_version
    ):
        raise ValueError("Versioned evidence references required")
    if now.tzinfo is None or (evidence.deadline and evidence.deadline.tzinfo is None):
        raise ValueError("Aware decision/deadline clocks required")
    if not math.isfinite(evidence.resource_lead_hours) or evidence.resource_lead_hours < 0:
        raise ValueError("Finite nonnegative resource lead time required")
    template = templates["investigate_sensor_departure"]
    if (
        not template.get("revision")
        or template.get("authority") != "fictional_demo_engineering_review"
    ):
        raise ValueError("Known versioned demo procedure required")
    reasons, margin = [], None
    urgency = "review"
    unsupported = evidence.coverage not in {"full", "partial"} or evidence.ood
    if unsupported:
        reasons.append("quality_or_distribution_requires_engineering_review")
    if evidence.confirmed_constraint:
        urgency = "immediate"
        reasons.append("confirmed_constraint")
    if evidence.deadline:
        remaining = (evidence.deadline - now).total_seconds() / 3600
        if remaining <= 0:
            urgency = "immediate"
            reasons.append("hard_deadline_expired")
        elif remaining <= max(deadline_warning_hours, evidence.resource_lead_hours):
            if urgency != "immediate":
                urgency = "urgent"
            reasons.append("hard_deadline_before_resource_readiness")
    if evidence.interval_supported and not unsupported and evidence.lower_rul is not None:
        rate = evidence.operating_hours_per_calendar_hour
        if (
            evidence.rul_unit == "operating_hours"
            and evidence.track in {"synthetic_sensor_model", "synthetic_engine_demo"}
            and rate is not None
            and math.isfinite(rate)
            and 0 < rate <= 1
            and evidence.usage_mapping_version
            and math.isfinite(evidence.lower_rul)
            and evidence.lower_rul >= 0
        ):
            margin = evidence.lower_rul / rate - evidence.resource_lead_hours
            if margin < 0:
                if urgency != "immediate":
                    urgency = "urgent"
                reasons.append("lower_rul_resource_margin_negative")
        else:
            reasons.append("calendar_margin_unavailable_incompatible_units_or_mapping")
    p = evidence.calibrated_risk
    if (
        evidence.probability_display_enabled
        and not unsupported
        and p is not None
        and math.isfinite(p)
        and 0 <= p <= 1
        and p >= high_risk
    ):
        if urgency != "immediate":
            urgency = "urgent"
        reasons.append("high_supported_calibrated_horizon_risk")
    elif p is not None and not evidence.probability_display_enabled:
        reasons.append("probability_display_disabled")
    if evidence.persistent_anomaly:
        reasons.append("persistent_anomaly_investigation")
    if not reasons:
        reasons.append("no_trigger_defer_only_after_engineering_review")
    return dict(
        state="engineering_review",
        urgency=urgency,
        rationale=reasons,
        action="investigate",
        calendar_margin_hours=margin,
        policy_version=policy_version,
        evidence_ids=list(evidence.evidence_ids),
        model_version=evidence.model_version,
        feature_version=evidence.feature_version,
        procedure_code="investigate_sensor_departure",
        procedure_revision=template["revision"],
        required_resources=dict(
            skills=list(template["skills"]),
            parts=dict(template["parts"]),
            duration_hours=template["duration_hours"],
        ),
        approves_procedure=False,
        changes_serviceability=False,
    )
