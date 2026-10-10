from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_domain.health import ComponentEvidence, aircraft_health, component_health

NOW = datetime(2026, 10, 10, tzinfo=UTC)
EVIDENCE = ComponentEvidence(
    "engine",
    "synthetic_engine_demo",
    24,
    "operating_hours",
    NOW,
    "a" * 64,
    "full",
    True,
    0.82,
    True,
)
ARGS = dict(
    now=NOW,
    expected_bundle="a" * 64,
    track="synthetic_engine_demo",
    horizon=24,
    unit="operating_hours",
)


def test_transparent_indicator_and_no_mutation():
    result = component_health(EVIDENCE, **ARGS)
    assert result["score"] == pytest.approx(18)
    assert result["category"] == "critical"
    assert not hasattr(EVIDENCE, "serviceability")
    assert "serviceability" not in result


@pytest.mark.parametrize(
    "changes",
    [
        {"probability_display_enabled": False},
        {"essential_observed": False},
        {"unit": "cycles"},
        {"horizon": 30},
        {"bundle_hash": "b" * 64},
        {"ood": True},
        {"as_of": NOW - timedelta(hours=7)},
        {"as_of": NOW + timedelta(seconds=1)},
        {"calibrated_probability": float("nan")},
    ],
)
def test_unsupported_cannot_be_healthy(changes):
    result = component_health(replace(EVIDENCE, **changes), **ARGS)
    assert result["score"] is None and result["category"] == "unknown"


def test_partial_coverage_minimum_and_absent_essential():
    other = replace(EVIDENCE, component_id="aux", calibrated_probability=0.2, coverage="partial")
    result = aircraft_health(
        [EVIDENCE, other],
        essential_components={"engine"},
        critical_components={"engine", "aux"},
        **ARGS,
    )
    assert result["score"] == pytest.approx(18)
    assert result["coverage"] == "partial"
    result = aircraft_health(
        [other], essential_components={"engine"}, critical_components={"engine", "aux"}, **ARGS
    )
    assert result["score"] is None


def test_incompatible_essential_signature_and_duplicate_versions():
    result = aircraft_health(
        [replace(EVIDENCE, horizon=30)],
        essential_components={"engine"},
        critical_components={"engine"},
        **ARGS,
    )
    assert result["score"] is None
    with pytest.raises(ValueError, match="one versioned"):
        aircraft_health(
            [EVIDENCE, EVIDENCE],
            essential_components={"engine"},
            critical_components={"engine"},
            **ARGS,
        )
