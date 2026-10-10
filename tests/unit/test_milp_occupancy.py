import pyomo.environ as pyo
import pytest
from fleetiq_scheduling.inputs import Baseline
from fleetiq_scheduling.milp import build_model, require_executable
from pyomo.contrib.appsi.base import TerminationCondition
from pyomo.contrib.appsi.solvers import Highs
from test_greedy_scheduler import job
from test_planning_inputs import snapshot


def solve_fixed(s, assignments):
    m = build_model(s)
    for j, t in m.JT:
        m.x[j, t].fix(int(assignments.get(j) == t))
    for j in m.J:
        if not m.u[j].fixed:
            m.u[j].fix(int(j not in assignments))
    solver = Highs()
    solver.config.load_solution = False
    solver.config.time_limit = 10
    results = solver.solve(m)
    assert results.termination_condition == TerminationCondition.optimal
    results.solution_loader.load_vars()
    return m


def test_aircraft_downtime_is_interval_union_not_sum():
    s = snapshot(
        (
            job("one", duration_hours=6, skills=(), parts=()),
            job("two", duration_hours=4, skills=(), parts=()),
        )
    )
    m = solve_fixed(s, {"one": 0, "two": 1})
    assert sum(pyo.value(m.z[j, k]) for j in m.J for k in m.K) == 5
    assert sum(pyo.value(m.y["a", k]) for k in m.K) == 3
    assert sum(pyo.value(m.v["a", k]) for k in m.K) == 81
    for k in m.K:
        assert pyo.value(m.y["a", k]) == int(k in {0, 1, 2})


def test_baseline_or_occupancy_truth_table_and_empty_aircraft():
    s = snapshot(
        (job("one", duration_hours=4, skills=(), parts=()),),
        baseline=[
            Baseline("a", tuple(k in {1, 5} for k in range(84))),
            Baseline("empty", (False,) * 84),
        ],
    )
    m = solve_fixed(s, {"one": 0})
    for k in m.K:
        assert pyo.value(m.v["a", k]) == int(k not in {0, 1, 5})
        assert pyo.value(m.y["empty", k]) == 0 and pyo.value(m.v["empty", k]) == 1


def test_mandatory_cannot_omit_and_optional_omission_has_zero_occupancy():
    s = snapshot(
        (
            job("mandatory", skills=(), parts=()),
            job("optional", mandatory=False, skills=(), parts=()),
        )
    )
    m = solve_fixed(s, {"mandatory": 0})
    assert m.u["mandatory"].fixed and pyo.value(m.u["mandatory"]) == 0
    assert pyo.value(m.u["optional"]) == 1
    assert all(pyo.value(m.z["optional", k]) == 0 for k in m.K)
    assert not hasattr(m, "s")
    assert m.I["part", "s", 0].lb == 0
    with pytest.raises(ValueError, match="not an executable"):
        require_executable(m)


def test_locked_started_job_covers_remaining_horizon_and_optional_empty_window():
    s = snapshot(
        (
            job(
                "started", state="executing", locked_start=-1, duration_hours=6, skills=(), parts=()
            ),
            job("optional", mandatory=False, candidates=(), skills=(), parts=()),
        )
    )
    m = solve_fixed(s, {"started": -1})
    assert pyo.value(m.y["a", 0]) == pyo.value(m.y["a", 1]) == 1
    assert pyo.value(m.y["a", 2]) == 0 and pyo.value(m.u["optional"]) == 1


def test_fixed_omission_of_mandatory_job_is_infeasible():
    s = snapshot((job("mandatory", candidates=(0,), skills=(), parts=()),))
    m = build_model(s)
    m.x["mandatory", 0].fix(0)
    solver = Highs()
    solver.config.load_solution = False
    result = solver.solve(m)
    assert result.termination_condition == TerminationCondition.infeasible
    assert result.best_feasible_objective is None
