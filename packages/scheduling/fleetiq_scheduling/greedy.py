"""Deterministic proposal-only baseline and independent constraint replay."""

from dataclasses import dataclass

from fleetiq_scheduling.inputs import named_staff


def validate(snapshot, assignments, *, complete=True):
    errors = []
    jobs = snapshot.jobs
    for jid, start in assignments.items():
        if jid not in jobs or start not in jobs[jid].allowed_starts:
            errors.append(f"invalid_start:{jid}")
    if errors:
        return tuple(errors)
    for jid, t in jobs.items():
        if complete and (t.job.mandatory or t.job.state != "pending") and jid not in assignments:
            errors.append(f"mandatory_unscheduled:{jid}")
        if jid not in assignments:
            continue
        for p in t.job.dependencies:
            if p not in assignments:
                if complete:
                    errors.append(f"missing_predecessor:{jid}:{p}")
            elif assignments[p] + jobs[p].duration_slots > assignments[jid]:
                errors.append(f"precedence:{p}:{jid}")
    for k in range(84):
        active = [
            t
            for jid, t in jobs.items()
            if jid in assignments and assignments[jid] <= k < assignments[jid] + t.duration_slots
        ]
        components = [t.job.component for t in active]
        if len(set(components)) != len(components):
            errors.append(f"component_overlap:{k}")
        for site in snapshot.sites:
            if sum(t.job.bay_need for t in active if t.job.site == site.id) > site.bays[k]:
                errors.append(f"bay_capacity:{site.id}:{k}")
        for pool in snapshot.pools:
            used = sum(
                dict(t.job.skills).get(pool.skill, 0) for t in active if t.job.site == pool.site
            )
            if used > snapshot.capacity(pool.site, pool.skill, k):
                errors.append(f"skill_capacity:{pool.site}:{pool.skill}:{k}")
        for stock in snapshot.stock:
            supply = (
                stock.usable
                - stock.reserved
                + sum(
                    r.quantity
                    for r in snapshot.receipts
                    if r.site == stock.site and r.part == stock.part and r.slot <= k
                )
            )
            demand = sum(
                dict(t.net_parts).get(stock.part, 0)
                for jid, t in jobs.items()
                if t.job.site == stock.site and jid in assignments and assignments[jid] <= k
            )
            if supply - demand < -1e-9:
                errors.append(f"part_shortage:{stock.site}:{stock.part}:{k}")
    try:
        named_staff(snapshot, assignments)
    except ValueError as e:
        errors.append("named_staff:" + str(e))
    return tuple(errors)


def availability(snapshot, assignments):
    return tuple(
        (
            b.aircraft,
            tuple(
                not b.unavailable[k]
                and not any(
                    t.job.aircraft == b.aircraft
                    and jid in assignments
                    and assignments[jid] <= k < assignments[jid] + t.duration_slots
                    for jid, t in snapshot.jobs.items()
                )
                for k in range(84)
            ),
        )
        for b in snapshot.baseline
    )


def cost(snapshot, assignments, backlog):
    jobs = snapshot.jobs
    direct = sum(
        jobs[j].job.maintenance_cost + jobs[j].job.delay_cost * max(0, t)
        for j, t in assignments.items()
    )
    omitted = sum(jobs[j].job.omission_cost for j in backlog if not jobs[j].job.mandatory)
    downtime = snapshot.downtime_cost * sum(
        not v for a, values in availability(snapshot, assignments) for v in values
    )
    return direct + omitted + downtime


@dataclass(frozen=True)
class Proposal:
    snapshot_hash: str
    assignments: tuple[tuple[str, int], ...]
    backlog: tuple[str, ...]
    conflicts: tuple[str, ...]
    trace: tuple[str, ...]
    cost: float
    cost_unit: str
    roster: tuple[tuple[str, tuple[str, ...]], ...]
    aircraft_availability: tuple[tuple[str, tuple[bool, ...]], ...]
    state: str = "proposal"

    @property
    def feasible(self):
        return not self.conflicts


def schedule(snapshot):
    jobs = snapshot.jobs
    assignments = {jid: t.job.locked_start for jid, t in jobs.items() if t.job.state != "pending"}
    trace = [f"locked:{jid}:{start}" for jid, start in sorted(assignments.items())]
    locked_errors = validate(snapshot, assignments, complete=False)
    backlog = []
    conflicts = list(locked_errors)
    remaining = set(jobs) - set(assignments)

    def priority(jid):
        t = jobs[jid]
        return (t.job.urgency, max(t.allowed_starts, default=84) + t.duration_slots, jid)

    while remaining and not locked_errors:
        ready = [j for j in remaining if all(p not in remaining for p in jobs[j].job.dependencies)]
        if not ready:
            raise ValueError("Unvalidated dependency cycle")
        jid = min(ready, key=priority)
        remaining.remove(jid)
        t = jobs[jid]
        if any(p not in assignments for p in t.job.dependencies):
            backlog.append(jid)
            trace.append(f"backlog:{jid}:predecessor_unscheduled")
            if t.job.mandatory:
                conflicts.append(f"mandatory_predecessor_unscheduled:{jid}")
            continue
        reasons = set()
        for start in t.allowed_starts:
            trial = assignments | {jid: start}
            errors = validate(snapshot, trial, complete=False)
            if not errors:
                assignments = trial
                trace.append(f"assigned:{jid}:{start}:earliest_feasible")
                break
            reasons.update(e.split(":", 1)[0] for e in errors)
        else:
            backlog.append(jid)
            summary = ",".join(sorted(reasons)) or "no_allowed_start"
            trace.append(f"backlog:{jid}:{summary}")
            if t.job.mandatory:
                conflicts.append(f"mandatory_infeasible:{jid}:{summary}")
    backlog.extend(sorted(remaining))
    conflicts.extend(validate(snapshot, assignments))
    try:
        roster = tuple(sorted(named_staff(snapshot, assignments).items()))
    except ValueError:
        roster = ()
    return Proposal(
        snapshot.hash,
        tuple(sorted(assignments.items())),
        tuple(backlog),
        tuple(sorted(set(conflicts))),
        tuple(trace),
        cost(snapshot, assignments, backlog),
        snapshot.cost_unit,
        roster,
        availability(snapshot, assignments),
    )
