"""Step 075 occupancy core. Missing Step 076 constraints prevent executable approval."""

import pyomo.environ as pyo

MISSING_CONSTRAINTS = (
    "bay_capacity",
    "skill_capacity",
    "inventory_balance",
    "precedence",
    "component_exclusion",
    "capability_demand",
    "production_objective",
)


def build_model(snapshot):
    model = pyo.ConcreteModel(name="FleetIQ occupancy core — nonapprovable")
    model.snapshot_hash = snapshot.hash
    model.formulation_scope = "occupancy_core_only"
    model.missing_constraints = MISSING_CONSTRAINTS
    model.executable = False
    model.J = pyo.Set(initialize=tuple(snapshot.jobs), ordered=True)
    model.K = pyo.Set(initialize=range(84), ordered=True)
    model.A = pyo.Set(initialize=tuple(b.aircraft for b in snapshot.baseline), ordered=True)
    model.JT = pyo.Set(
        dimen=2,
        initialize=[(jid, start) for jid, t in snapshot.jobs.items() for start in t.allowed_starts],
        ordered=True,
    )
    model.MH = pyo.Set(dimen=2, initialize=[(s.part, s.site) for s in snapshot.stock], ordered=True)
    model.x = pyo.Var(model.JT, domain=pyo.Binary)
    model.u = pyo.Var(model.J, domain=pyo.Binary)
    model.z = pyo.Var(model.J, model.K, domain=pyo.Binary)
    model.y = pyo.Var(model.A, model.K, domain=pyo.Binary)
    model.v = pyo.Var(model.A, model.K, domain=pyo.Binary)
    # Intentionally no inventory balance until Step 076: these are variables, not evidence.
    model.I = pyo.Var(model.MH, model.K, domain=pyo.NonNegativeReals)
    for jid, task in snapshot.jobs.items():
        if task.job.mandatory or task.job.state != "pending":
            model.u[jid].fix(0)

    def selection(m, jid):
        return sum(m.x[jid, t] for t in snapshot.jobs[jid].allowed_starts) + m.u[jid] == 1

    model.start_once = pyo.Constraint(model.J, rule=selection)

    def active(m, jid, k):
        task = snapshot.jobs[jid]
        return m.z[jid, k] == sum(
            m.x[jid, t] for t in task.allowed_starts if t <= k < t + task.duration_slots
        )

    model.occupancy = pyo.Constraint(model.J, model.K, rule=active)
    model.union_lower = pyo.ConstraintList()
    for jid, task in snapshot.jobs.items():
        for k in range(84):
            model.union_lower.add(model.y[task.job.aircraft, k] >= model.z[jid, k])

    def union_upper(m, aircraft, k):
        return m.y[aircraft, k] <= sum(
            m.z[jid, k] for jid, t in snapshot.jobs.items() if t.job.aircraft == aircraft
        )

    model.union_upper = pyo.Constraint(model.A, model.K, rule=union_upper)
    baseline = {b.aircraft: b.unavailable for b in snapshot.baseline}
    model.available_baseline = pyo.Constraint(
        model.A, model.K, rule=lambda m, a, k: m.v[a, k] <= 1 - int(baseline[a][k])
    )
    model.available_jobs = pyo.Constraint(
        model.A, model.K, rule=lambda m, a, k: m.v[a, k] <= 1 - m.y[a, k]
    )
    model.available_lower = pyo.Constraint(
        model.A, model.K, rule=lambda m, a, k: m.v[a, k] >= 1 - int(baseline[a][k]) - m.y[a, k]
    )
    # Feasibility objective only; no application optimality claim from the occupancy core.
    model.objective = pyo.Objective(expr=0)
    return model


def require_executable(model):
    if not model.executable or model.missing_constraints:
        raise ValueError("Incomplete occupancy core is not an executable or approvable schedule")
