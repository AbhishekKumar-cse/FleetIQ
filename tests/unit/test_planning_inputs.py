from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_scheduling.calendar import Calendar
from fleetiq_scheduling.inputs import (
    Baseline,
    Job,
    Pool,
    Receipt,
    Site,
    Staff,
    Stock,
    build_snapshot,
    named_staff,
)

CAL = Calendar(datetime(2026, 1, 1, tzinfo=UTC))
JOB = Job(
    "j",
    "a",
    "c",
    "s",
    "procedure-1",
    "evidence-1",
    3,
    skills=(("engine", 1),),
    parts=(("part", 1),),
)


def snapshot(jobs=(JOB,), **changes):
    kwargs = dict(
        jobs=jobs,
        sites=[Site("s", (1,) * 84)],
        staff=[Staff("person", ("engine", "avionics"), (True,) * 84)],
        pools=[Pool("s", "engine", ("person",))],
        stock=[Stock("s", "part", 1, 0)],
        baseline=[Baseline("a", (False,) * 84)],
        source_version="fixture-v1",
        baseline_excludes_candidate_jobs=True,
    )
    return build_snapshot(CAL, **(kwargs | changes))


def test_rounding_deadline_and_immutable_source_snapshot():
    result = snapshot((replace(JOB, deadline=CAL.starts_at + timedelta(hours=6)),))
    assert result.jobs["j"].duration_slots == 2 and result.jobs["j"].allowed_starts == (0, 1)
    assert (
        result.hash == snapshot((replace(JOB, deadline=CAL.starts_at + timedelta(hours=6)),)).hash
    )
    with pytest.raises(FrozenInstanceError):
        result.tasks = ()
    with pytest.raises(TypeError):
        result.jobs["other"] = result.tasks[0]
    capacities = [1] * 84
    frozen = snapshot(sites=[Site("s", capacities)])
    capacities[0] = 99
    assert frozen.sites[0].bays[0] == 1


def test_reject_double_counted_multi_skill_staff_and_missing_qualification():
    with pytest.raises(ValueError, match="multiple pools"):
        snapshot(pools=[Pool("s", "engine", ("person",)), Pool("s", "avionics", ("person",))])
    with pytest.raises(ValueError, match="qualified"):
        snapshot(pools=[Pool("s", "engine", ("unknown",))])


def test_dependency_cycles_and_invalid_deadlines():
    with pytest.raises(ValueError, match="cycle"):
        snapshot((replace(JOB, dependencies=("k",)), replace(JOB, id="k", dependencies=("j",))))
    with pytest.raises(ValueError, match="no domain-valid"):
        snapshot((replace(JOB, deadline=CAL.starts_at + timedelta(hours=1)),))
    with pytest.raises(ValueError, match="in-horizon"):
        snapshot((replace(JOB, candidates=(84,)),))


def test_only_confirmed_receipts_and_own_reservations_accounted_once():
    with pytest.raises(ValueError, match="confirmed"):
        snapshot(receipts=[Receipt("r", "s", "part", 2, 1, False)])
    result = snapshot(
        (replace(JOB, reserved_parts=(("part", 1),)),),
        stock=[Stock("s", "part", 1, 1)],
        receipts=[Receipt("r", "s", "part", 2, 1, True)],
    )
    assert result.tasks[0].net_parts == ()
    with pytest.raises(ValueError, match="reserved balance"):
        snapshot((replace(JOB, reserved_parts=(("part", 1),)),))


def test_started_and_approved_jobs_are_locked():
    assert snapshot((replace(JOB, state="approved", locked_start=3),)).tasks[0].allowed_starts == (
        3,
    )
    started = snapshot((replace(JOB, state="executing", locked_start=-1),))
    assert started.tasks[0].allowed_starts == (-1,) and started.tasks[0].net_parts == ()
    with pytest.raises(ValueError, match="must be locked"):
        snapshot((replace(JOB, state="approved"),))


def test_named_staff_continuity_follows_aggregate_capacity():
    result = snapshot()
    assert named_staff(result, {"j": 0})["j"] == ("person",)
    alternating = snapshot(
        staff=[
            Staff("person", ("engine",), tuple(k % 2 == 0 for k in range(84))),
            Staff("other", ("engine",), tuple(k % 2 == 1 for k in range(84))),
        ],
        pools=[Pool("s", "engine", ("person", "other"))],
    )
    assert alternating.capacity("s", "engine", 0) == alternating.capacity("s", "engine", 1) == 1
    with pytest.raises(ValueError, match="continuous"):
        named_staff(alternating, {"j": 0})
