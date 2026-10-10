from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fleetiq_domain.maintenance_decisions import DecisionEvidence, prioritize

NOW = datetime(2026, 10, 10, tzinfo=UTC)
TEMPLATES = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "config/task_templates.yaml").read_text()
)["templates"]
EVIDENCE = DecisionEvidence(("prediction-1",), "model-v1", "features-v1", coverage="full")


def decision(evidence):
    return prioritize(evidence, now=NOW, templates=TEMPLATES)


def test_hard_deadline_precedes_risk_and_does_not_approve():
    e = replace(
        EVIDENCE,
        deadline=NOW - timedelta(hours=1),
        calibrated_risk=0.9,
        probability_display_enabled=True,
    )
    result = decision(e)
    assert result["urgency"] == "immediate"
    assert result["state"] == "engineering_review"
    assert result["approves_procedure"] is False and result["changes_serviceability"] is False


def test_negative_resource_margin_needs_explicit_hour_mapping():
    e = replace(
        EVIDENCE,
        lower_rul=4,
        interval_supported=True,
        rul_unit="operating_hours",
        operating_hours_per_calendar_hour=0.5,
        usage_mapping_version="fictional-usage-v1",
        resource_lead_hours=10,
    )
    result = decision(e)
    assert result["calendar_margin_hours"] == -2 and result["urgency"] == "urgent"
    for changes in (
        {"rul_unit": "cycles"},
        {"usage_mapping_version": None},
        {"track": "cmapss_benchmark"},
        {"interval_supported": False},
    ):
        result = decision(replace(e, **changes))
        assert result["calendar_margin_hours"] is None


def test_ood_and_disabled_probability_request_review_not_deferral():
    result = decision(
        replace(EVIDENCE, ood=True, calibrated_risk=0.99, probability_display_enabled=True)
    )
    assert result["urgency"] == "review" and result["state"] == "engineering_review"
    result = decision(
        replace(
            EVIDENCE,
            calibrated_risk=0.99,
            probability_display_enabled=False,
            persistent_anomaly=True,
        )
    )
    assert (
        result["urgency"] == "review" and "persistent_anomaly_investigation" in result["rationale"]
    )


def test_versions_resources_and_repeat_determinism():
    first = decision(replace(EVIDENCE, confirmed_constraint=True))
    assert first == decision(replace(EVIDENCE, confirmed_constraint=True))
    assert first["required_resources"] == dict(
        skills=["engine_diagnostics"], parts={}, duration_hours=2
    )
    with pytest.raises(ValueError, match="Versioned"):
        decision(replace(EVIDENCE, evidence_ids=()))
