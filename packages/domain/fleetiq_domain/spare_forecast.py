"""Read-only, explicitly hypothetical demand bands; no procurement or stock mutation."""

import math
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from fleetiq_domain.intermittent_demand import sba_croston


@dataclass(frozen=True)
class RiskDemand:
    task_id: str
    part_id: str
    quantity: float
    horizon_hours: float
    probability: float | None
    calibrated_supported: bool
    evidence_version: str
    workload_group: str
    already_reserved: bool = False


@dataclass(frozen=True)
class Inbound:
    reference: str
    quantity: float
    arrives_at: object
    confirmed: bool


def forecast(
    *,
    part_id,
    demands,
    as_of,
    usable,
    reserved,
    inbound=(),
    lead_hours=24,
    review_hours=24,
    lead_max_hours=None,
    service_level=0.95,
    pack_size=1,
    correlation=0.35,
    seed=26249,
    scenarios=5000,
    history=(),
    history_bin_hours=24,
    policy_version="logistics-demo-v1",
):
    maximum = lead_hours if lead_max_hours is None else lead_max_hours
    horizon = maximum + review_hours
    numbers = (usable, reserved, lead_hours, review_hours, maximum, pack_size, history_bin_hours)
    if (
        as_of.tzinfo is None
        or any(not math.isfinite(float(v)) or v < 0 for v in numbers)
        or reserved > usable
        or maximum < lead_hours
        or horizon <= 0
        or pack_size <= 0
        or history_bin_hours <= 0
        or not 0 < service_level < 1
        or not 0 <= correlation < 1
        or not isinstance(scenarios, int)
        or not 100 <= scenarios <= 100000
    ):
        raise ValueError("Valid aware forecast horizon, stock and scenario policy required")
    demands = tuple(demands)
    if len({d.task_id for d in demands}) != len(demands):
        raise ValueError("Duplicate task/horizon demand cannot be counted twice")
    for d in demands:
        if (
            not d.task_id
            or not d.evidence_version
            or not d.workload_group
            or d.part_id != part_id
            or not math.isfinite(d.quantity)
            or d.quantity <= 0
            or not math.isfinite(d.horizon_hours)
            or d.horizon_hours != horizon
        ):
            raise ValueError(
                "Matching part/horizon and versioned positive task quantities required"
            )
        if d.probability is not None and (
            not math.isfinite(d.probability) or not 0 <= d.probability <= 1
        ):
            raise ValueError("Invalid marginal probability")
    if len({r.reference for r in inbound}) != len(inbound):
        raise ValueError("Duplicate inbound receipt")
    for r in inbound:
        if (
            not r.reference
            or r.arrives_at.tzinfo is None
            or not math.isfinite(r.quantity)
            or r.quantity <= 0
        ):
            raise ValueError("Valid inbound quantities and aware arrival times required")
    confirmed = sum(
        r.quantity
        for r in inbound
        if r.confirmed and as_of <= r.arrives_at <= as_of + timedelta(hours=horizon)
    )
    position = usable - reserved + confirmed
    baseline = sba_croston(history)
    result = dict(
        state="proposal",
        part_id=part_id,
        policy_version=policy_version,
        as_of=as_of.isoformat(),
        horizon_hours=horizon,
        service_level=service_level,
        stock_position=position,
        confirmed_inbound=confirmed,
        ignored_speculative_receipts=[r.reference for r in inbound if not r.confirmed],
        affected_jobs=[d.task_id for d in demands if not d.already_reserved],
        excluded_reserved_jobs=[d.task_id for d in demands if d.already_reserved],
        historical_baseline=baseline,
        assumptions=[
            "risk horizon equals maximum lead plus review period",
            "shared normal-copula workload scenario preserves marginal event probabilities at the horizon",
            "uniform conditional event time and uniform uncertain lead time are scenario assumptions",
            "historical demand is a separate comparison, never added to risk demand",
            "stock position subtracts existing reservations; proposals do not mutate inventory",
        ],
    )
    active = [d for d in demands if not d.already_reserved]
    unsupported = [d.task_id for d in active if not d.calibrated_supported or d.probability is None]
    if unsupported:
        return result | dict(
            state="needs_review",
            unsupported_jobs=unsupported,
            expected_demand=None,
            demand_bands=None,
            reorder_point=None,
            proposed_quantity=None,
        )
    rng = np.random.default_rng(seed)
    leads = rng.uniform(lead_hours, maximum, scenarios) + review_hours
    groups = {g: rng.normal(size=scenarios) for g in sorted({d.workload_group for d in active})}
    demand = np.zeros(scenarios)
    # math.erf avoids a SciPy dependency and supports exact p=0/1 boundaries.
    for d in active:
        z = math.sqrt(correlation) * groups[d.workload_group] + math.sqrt(
            1 - correlation
        ) * rng.normal(size=scenarios)
        uniforms = np.array([0.5 * (1 + math.erf(float(v) / math.sqrt(2))) for v in z])
        occurs = uniforms < d.probability
        within = rng.uniform(0, horizon, scenarios) <= leads
        demand += d.quantity * (occurs & within)
    bands = {
        str(q): float(np.quantile(demand, q, method="higher")) for q in (0.5, 0.9, service_level)
    }
    reorder = float(np.quantile(demand, service_level, method="higher"))
    quantity = math.ceil(max(0, reorder - position) / pack_size) * pack_size
    if baseline["supported"]:
        # Separate Poisson count reference; stationary independent arrivals are hypothetical.
        counts = rng.poisson(baseline["rate_per_bin"] * leads / history_bin_hours)
        result["historical_baseline"] = baseline | dict(
            protection_period_quantile=float(np.quantile(counts, service_level, method="higher")),
            comparison_only=True,
        )
    return result | dict(
        expected_demand=sum(d.probability * d.quantity for d in active),
        demand_bands=bands,
        reorder_point=reorder,
        proposed_quantity=quantity,
        simulation_seed=seed,
        scenario_count=scenarios,
        correlation=correlation,
    )
