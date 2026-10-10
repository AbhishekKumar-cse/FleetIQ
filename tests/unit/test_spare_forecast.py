from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_domain.intermittent_demand import sba_croston
from fleetiq_domain.spare_forecast import Inbound, RiskDemand, forecast

AT = datetime(2026, 1, 1, tzinfo=UTC)
D = RiskDemand("task-1", "part", 2, 48, 0.5, True, "cal-demo-1", "workload-1")


def run(demands=(D,), **changes):
    return forecast(part_id="part", demands=demands, as_of=AT, usable=0, reserved=0, **changes)


def test_marginal_expected_demand_and_reserved_task_exclusion():
    report = run((D, replace(D, task_id="task-2", already_reserved=True)))
    assert report["expected_demand"] == 1
    assert report["excluded_reserved_jobs"] == ["task-2"]
    assert report == run((D, replace(D, task_id="task-2", already_reserved=True)))


def test_unsupported_is_unknown_not_zero_and_incompatible_horizons_rejected():
    report = run((replace(D, calibrated_supported=False),))
    assert report["state"] == "needs_review" and report["proposed_quantity"] is None
    assert report["expected_demand"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        run((D, replace(D, horizon_hours=24)))
    with pytest.raises(ValueError, match="Matching"):
        run((replace(D, horizon_hours=24),))


def test_confirmed_receipts_only_and_pack_rounding():
    incoming = (
        Inbound("firm", 1, AT + timedelta(hours=6), True),
        Inbound("speculative", 100, AT + timedelta(hours=3), False),
    )
    report = run((replace(D, probability=1, quantity=4),), inbound=incoming, pack_size=2)
    assert report["stock_position"] == 1 and report["proposed_quantity"] == 4
    assert report["ignored_speculative_receipts"] == ["speculative"]


def test_zero_intervals_and_sba_reference_support():
    assert sba_croston([0] * 20)["rate_per_bin"] is None
    assert sba_croston([0, 2, 0, 0] * 8)["supported"]
    assert sba_croston([2] * 20)["rate_per_bin"] == pytest.approx(1.9)
    assert run((replace(D, probability=0),))["proposed_quantity"] == 0
    with pytest.raises(ValueError):
        sba_croston([float("nan")])


def test_correlated_workload_and_lead_scenarios_are_disclosed_and_repeatable():
    demands = tuple(replace(D, task_id=f"task-{i}", horizon_hours=72) for i in range(12))
    correlated = run(demands, lead_max_hours=48, correlation=0.8)
    independent = run(demands, lead_max_hours=48, correlation=0)
    assert correlated["expected_demand"] == 12
    assert correlated["reorder_point"] > independent["reorder_point"]
    assert "scenario assumptions" in correlated["assumptions"][2]


def test_stock_and_receipt_input_integrity():
    with pytest.raises(ValueError):
        forecast(part_id="part", demands=[D], as_of=AT, usable=1, reserved=2)
    with pytest.raises(ValueError, match="Duplicate inbound"):
        run(inbound=[Inbound("same", 1, AT, True)] * 2)
