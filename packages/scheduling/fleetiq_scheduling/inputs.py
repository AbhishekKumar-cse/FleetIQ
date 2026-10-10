"""Immutable, versioned scheduling contracts; snapshots perform no live writes."""

import math
from dataclasses import asdict, dataclass
from types import MappingProxyType

from fleetiq_evaluation.splits import content_hash

from fleetiq_scheduling.calendar import Calendar


@dataclass(frozen=True)
class Staff:
    id: str
    qualifications: tuple[str, ...]
    available: tuple[bool, ...]


@dataclass(frozen=True)
class Pool:
    site: str
    skill: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class Site:
    id: str
    bays: tuple[int, ...]


@dataclass(frozen=True)
class Stock:
    site: str
    part: str
    usable: float
    reserved: float


@dataclass(frozen=True)
class Receipt:
    id: str
    site: str
    part: str
    slot: int
    quantity: float
    confirmed: bool


@dataclass(frozen=True)
class Baseline:
    aircraft: str
    unavailable: tuple[bool, ...]


@dataclass(frozen=True)
class Job:
    id: str
    aircraft: str
    component: str
    site: str
    procedure_revision: str
    evidence_version: str
    duration_hours: float
    skills: tuple[tuple[str, int], ...] = ()
    parts: tuple[tuple[str, float], ...] = ()
    reserved_parts: tuple[tuple[str, float], ...] = ()
    dependencies: tuple[str, ...] = ()
    bay_need: int = 1
    urgency: int = 2
    mandatory: bool = True
    earliest: object = None
    deadline: object = None
    candidates: tuple[int, ...] | None = None
    state: str = "pending"
    locked_start: int | None = None
    maintenance_cost: float = 1
    delay_cost: float = 0
    omission_cost: float = 1000


@dataclass(frozen=True)
class Task:
    job: Job
    duration_slots: int
    allowed_starts: tuple[int, ...]
    net_parts: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Snapshot:
    calendar: Calendar
    tasks: tuple[Task, ...]
    sites: tuple[Site, ...]
    staff: tuple[Staff, ...]
    pools: tuple[Pool, ...]
    stock: tuple[Stock, ...]
    receipts: tuple[Receipt, ...]
    baseline: tuple[Baseline, ...]
    source_version: str
    baseline_excludes_candidate_jobs: bool
    cost_unit: str = "demo_cost_units"
    downtime_cost: float = 1

    @property
    def hash(self):
        # Canonical serializer preserves aware datetimes as ISO text.
        def plain(value):
            if isinstance(value, dict):
                return {k: plain(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [plain(v) for v in value]
            return value.isoformat() if hasattr(value, "isoformat") else value

        return content_hash(plain(asdict(self)))

    @property
    def jobs(self):
        return MappingProxyType({t.job.id: t for t in self.tasks})

    def capacity(self, site, skill, k):
        staff = {s.id: s for s in self.staff}
        pool = next(p for p in self.pools if p.site == site and p.skill == skill)
        return sum(staff[i].available[k] for i in pool.members)


def pairs(values, *, integer=False):
    if len({k for k, v in values}) != len(values) or any(
        not k or not math.isfinite(v) or v <= 0 or (integer and type(v) is not int)
        for k, v in values
    ):
        raise ValueError("Unique positive finite resource demand required")


def build_snapshot(
    calendar,
    *,
    jobs,
    sites,
    staff,
    pools,
    stock,
    receipts=(),
    baseline,
    source_version,
    baseline_excludes_candidate_jobs,
    downtime_cost=1,
    cost_unit="demo_cost_units",
):
    # A frozen dataclass alone would still permit nested mutable caller lists.
    jobs = tuple(
        Job(
            **(
                asdict(j)
                | {
                    k: tuple(tuple(v) if isinstance(v, list) else v for v in getattr(j, k))
                    for k in ("skills", "parts", "reserved_parts", "dependencies")
                }
                | {"candidates": tuple(j.candidates) if j.candidates is not None else None}
            )
        )
        for j in jobs
    )
    sites = tuple(Site(s.id, tuple(s.bays)) for s in sites)
    staff = tuple(Staff(s.id, tuple(s.qualifications), tuple(s.available)) for s in staff)
    pools = tuple(Pool(p.site, p.skill, tuple(p.members)) for p in pools)
    stock = tuple(stock)
    receipts = tuple(receipts)
    baseline = tuple(Baseline(b.aircraft, tuple(b.unavailable)) for b in baseline)
    if (
        not source_version
        or not baseline_excludes_candidate_jobs
        or not cost_unit
        or not math.isfinite(downtime_cost)
        or downtime_cost < 0
    ):
        raise ValueError("Version, cost unit and candidate-excluded baseline required")
    if (
        len({j.id for j in jobs}) != len(jobs)
        or len({s.id for s in sites}) != len(sites)
        or len({s.id for s in staff}) != len(staff)
        or len({(p.site, p.skill) for p in pools}) != len(pools)
    ):
        raise ValueError("Duplicate job/site/staff/pool")
    site_ids = {s.id for s in sites}
    staff_by_id = {s.id: s for s in staff}
    for s in sites:
        if not s.id or len(s.bays) != 84 or any(type(v) is not int or v < 0 for v in s.bays):
            raise ValueError("84 nonnegative bay capacities required")
    for s in staff:
        if not s.id or len(s.available) != 84 or any(type(v) is not bool for v in s.available):
            raise ValueError("84 boolean named-staff availability slots required")
    membership = []
    for p in pools:
        if (
            p.site not in site_ids
            or not p.skill
            or any(
                i not in staff_by_id or p.skill not in staff_by_id[i].qualifications
                for i in p.members
            )
        ):
            raise ValueError("Known qualified disjoint pool members required")
        membership.extend(p.members)
    if len(set(membership)) != len(membership):
        raise ValueError("Multi-skilled staff cannot be counted in multiple pools/sites")
    if len({(s.site, s.part) for s in stock}) != len(stock) or any(
        s.site not in site_ids
        or not s.part
        or any(not math.isfinite(v) or v < 0 for v in (s.usable, s.reserved))
        or s.reserved > s.usable
        for s in stock
    ):
        raise ValueError("Known nonnegative usable-minus-reserved stock required")
    stock_keys = {(s.site, s.part) for s in stock}
    if len({r.id for r in receipts}) != len(receipts) or any(
        not r.id
        or not r.confirmed
        or (r.site, r.part) not in stock_keys
        or type(r.slot) is not int
        or not 0 <= r.slot < 84
        or not math.isfinite(r.quantity)
        or r.quantity <= 0
        for r in receipts
    ):
        raise ValueError("Only unique confirmed in-horizon compatible receipts allowed")
    aircraft = {j.aircraft for j in jobs}
    if (
        len({b.aircraft for b in baseline}) != len(baseline)
        or not aircraft <= {b.aircraft for b in baseline}
        or any(
            len(b.unavailable) != 84 or any(type(v) is not bool for v in b.unavailable)
            for b in baseline
        )
    ):
        raise ValueError("Explicit 84-slot baseline for every aircraft required")
    tasks = []
    held = {key: 0.0 for key in stock_keys}
    ids = {j.id for j in jobs}
    for j in jobs:
        if (
            not all((j.id, j.aircraft, j.component, j.procedure_revision, j.evidence_version))
            or j.site not in site_ids
            or type(j.bay_need) is not int
            or j.bay_need < 0
            or type(j.urgency) is not int
            or j.urgency < 0
            or any(
                not math.isfinite(v) or v < 0
                for v in (j.maintenance_cost, j.delay_cost, j.omission_cost)
            )
        ):
            raise ValueError("Approved/versioned job and finite resource/cost inputs required")
        pairs(j.skills, integer=True)
        pairs(j.parts)
        pairs(j.reserved_parts)
        if any((j.site, k) not in {(p.site, p.skill) for p in pools} for k, v in j.skills) or any(
            (j.site, k) not in stock_keys for k, v in j.parts
        ):
            raise ValueError("Missing compatible resource/stock input")
        if len(set(j.dependencies)) != len(j.dependencies) or any(
            d not in ids or d == j.id for d in j.dependencies
        ):
            raise ValueError("Known distinct dependency predecessors required")
        duration = calendar.duration(j.duration_hours)
        allowed = calendar.starts(
            j.duration_hours, earliest=j.earliest, deadline=j.deadline, candidates=j.candidates
        )
        if j.state not in {"pending", "approved", "executing"}:
            raise ValueError("Only pending/approved/executing jobs can be planned")
        if j.state in {"approved", "executing"}:
            if type(j.locked_start) is not int:
                raise ValueError("Approved/started assignments must be locked")
            if j.state == "executing" and -duration < j.locked_start < 0:
                allowed = (j.locked_start,)
            elif j.locked_start not in allowed:
                raise ValueError("Locked assignment outside horizon/deadline")
            else:
                allowed = (j.locked_start,)
        elif j.locked_start is not None:
            raise ValueError("Unapproved job cannot claim a locked assignment")
        if not allowed and j.mandatory:
            raise ValueError("Mandatory job has no domain-valid allowed starts")
        own = dict(j.reserved_parts)
        if any(k not in dict(j.parts) or v > dict(j.parts)[k] for k, v in own.items()):
            raise ValueError("Reserved quantities exceed approved task scope")
        for k, v in own.items():
            held[j.site, k] += v
        net = (
            tuple((k, v - own.get(k, 0)) for k, v in j.parts if v - own.get(k, 0) > 0)
            if j.state != "executing"
            else ()
        )
        tasks.append(Task(j, duration, allowed, net))
    if any(held[s.site, s.part] > s.reserved for s in stock):
        raise ValueError("Candidate reservations exceed ledger reserved balance")
    visiting = set()
    done = set()
    by_id = {j.id: j for j in jobs}

    def visit(i):
        if i in visiting:
            raise ValueError("Dependency cycle")
        if i in done:
            return
        visiting.add(i)
        for p in by_id[i].dependencies:
            visit(p)
        visiting.remove(i)
        done.add(i)

    for j in jobs:
        visit(j.id)
    return Snapshot(
        calendar,
        tuple(tasks),
        sites,
        staff,
        pools,
        stock,
        receipts,
        baseline,
        source_version,
        True,
        cost_unit,
        downtime_cost,
    )


def named_staff(snapshot, assignments):
    """Validate named continuity after aggregate assignment; return proposed roster."""
    staff = {s.id: s for s in snapshot.staff}
    used = {s.id: set() for s in snapshot.staff}
    roster = {}
    for jid, start in sorted(assignments.items(), key=lambda p: (p[1], p[0])):
        task = snapshot.jobs[jid]
        if start not in task.allowed_starts:
            raise ValueError("Invalid assignment start")
        slots = set(range(max(0, start), min(84, start + task.duration_slots)))
        chosen = []
        for skill, count in task.job.skills:
            pool = next(p for p in snapshot.pools if p.site == task.job.site and p.skill == skill)
            candidates = [
                i
                for i in sorted(pool.members)
                if not used[i] & slots and all(staff[i].available[k] for k in slots)
            ]
            if len(candidates) < count:
                raise ValueError("No continuous named-staff assignment")
            for i in candidates[:count]:
                used[i] |= slots
                chosen.append(i)
        roster[jid] = tuple(chosen)
    return MappingProxyType(roster)
