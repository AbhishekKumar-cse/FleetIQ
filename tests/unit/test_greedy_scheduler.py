from dataclasses import replace

import pytest
from fleetiq_scheduling.greedy import schedule, validate
from fleetiq_scheduling.inputs import Receipt, Site, Stock
from test_planning_inputs import JOB, snapshot


def job(id, **changes):
    return replace(JOB, id=id, component="component-" + id, **changes)


def test_last_part_optional_backlog_and_no_live_mutation():
    s = snapshot(
        (job("urgent", urgency=0), job("optional", mandatory=False)),
        stock=[Stock("s", "part", 1, 0)],
    )
    before = s.hash
    p = schedule(s)
    assert p.feasible and p.assignments == (("urgent", 0),) and p.backlog == ("optional",)
    assert any("part_shortage" in r for r in p.trace)
    assert s.hash == before and p == schedule(s)
    assert not validate(s, dict(p.assignments))


def test_bay_skill_and_confirmed_receipt_produce_earliest_valid_starts():
    s = snapshot(
        (job("first", urgency=0), job("second")),
        receipts=[Receipt("firm", "s", "part", 3, 1, True)],
    )
    p = schedule(s)
    assert p.feasible and dict(p.assignments) == {"first": 0, "second": 3}
    assert dict(p.roster) == {"first": ("person",), "second": ("person",)}


def test_mandatory_deadline_conflict_and_same_component_exclusion():
    jobs = (job("one", candidates=(0,), urgency=0), job("two", candidates=(0,)))
    p = schedule(snapshot(jobs, stock=[Stock("s", "part", 2, 0)]))
    assert not p.feasible and "two" in p.backlog
    assert any("mandatory_infeasible" in c for c in p.conflicts)
    same = (
        replace(jobs[0], component="same", skills=()),
        replace(jobs[1], component="same", skills=()),
    )
    errors = validate(
        snapshot(same, sites=[Site("s", (2,) * 84)], stock=[Stock("s", "part", 2, 0)]),
        {"one": 0, "two": 0},
    )
    assert "component_overlap:0" in errors


def test_dependencies_and_locked_jobs_are_respected():
    s = snapshot(
        (
            job("later", dependencies=("early",), urgency=0),
            job("early", urgency=2, state="approved", locked_start=2),
        ),
        receipts=[Receipt("r", "s", "part", 0, 1, True)],
    )
    p = schedule(s)
    assert p.feasible and dict(p.assignments) == {"early": 2, "later": 4}
    assert any(c.startswith("precedence") for c in validate(s, {"early": 2, "later": 0}))


def test_optional_predecessor_omission_cannot_leave_executable_successor():
    s = snapshot(
        (job("parent", mandatory=False, candidates=()), job("child", dependencies=("parent",)))
    )
    p = schedule(s)
    assert not p.feasible and set(p.backlog) == {"parent", "child"}


def test_independent_validator_rejects_stock_and_unapproved_start():
    s = snapshot((job("j", candidates=(3,)),))
    assert validate(s, {"j": 2}) == ("invalid_start:j",)
    with pytest.raises(TypeError):
        s.jobs["new"] = s.tasks[0]
