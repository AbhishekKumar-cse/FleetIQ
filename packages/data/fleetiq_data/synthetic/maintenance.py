"""Resource-feasible historical work followed by independent inspection/release."""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pandas as pd

from fleetiq_data.synthetic.logistics import StockLedger


class MaintenanceHistory:
    def __init__(
        self,
        epoch: datetime,
        cutoff: datetime,
        stock: StockLedger,
        bays: int,
        mechanics: int,
        inspectors: int,
    ):
        if min(bays, mechanics, inspectors) < 1:
            raise ValueError("at least one bay, mechanic and inspector required")
        self.cutoff, self.stock = cutoff, stock
        self.bays = {f"DEMO-BAY-{n}": epoch for n in range(1, bays + 1)}
        self.mechanics = {f"DEMO-MECH-{n}": epoch for n in range(1, mechanics + 1)}
        self.inspectors = {f"DEMO-INSP-{n}": epoch for n in range(1, inspectors + 1)}
        self.tasks, self.events = [], []

    def exchange(
        self, aircraft_id: str, installation_id: str, confirmation: datetime
    ) -> datetime | None:
        if confirmation > self.cutoff:
            return None
        task_id = str(uuid5(NAMESPACE_URL, f"fleetiq:exchange:{installation_id}"))
        task = {
            "task_id": task_id,
            "aircraft_id": aircraft_id,
            "installation_id": installation_id,
            "template_code": "engine_exchange",
            "part_code": "DEMO-ENGINE",
            "quantity_required": 1,
            "queued_at": confirmation.isoformat(),
            "status": "queued",
            "blocked_reason": None,
        }
        bay = min(self.bays, key=self.bays.get)
        mechanic = min(self.mechanics, key=self.mechanics.get)
        inspector = min(self.inspectors, key=self.inspectors.get)
        start = max(confirmation + timedelta(hours=1), self.bays[bay], self.mechanics[mechanic])
        worked = start + timedelta(hours=2)
        inspection_start = max(worked, self.inspectors[inspector])
        inspected = inspection_start + timedelta(hours=1)
        release = inspected + timedelta(hours=1)
        if release > self.cutoff:
            task["blocked_reason"] = "capacity_or_cutoff"
        elif self.stock.available(start) < 1:
            task["blocked_reason"] = "spare_shortage"
        else:
            self.stock.issue(start, task_id)
            self.bays[bay], self.mechanics[mechanic] = release, worked
            self.inspectors[inspector] = inspected
            task["status"] = "released"
            for kind, at, worker in (
                ("work_started", start, mechanic),
                ("work_completed", worked, mechanic),
                ("inspection_started", inspection_start, inspector),
                ("inspection_passed", inspected, inspector),
                ("released", release, inspector),
            ):
                self.events.append(
                    {
                        "event_id": str(uuid5(NAMESPACE_URL, f"{task_id}:{kind}")),
                        "task_id": task_id,
                        "kind": kind,
                        "event_time": at.isoformat(),
                        "recorded_at": at.isoformat(),
                        "worker_id": worker,
                        "bay_id": bay,
                    }
                )
        self.tasks.append(task)
        return release if task["status"] == "released" else None

    def inspection_backlog(self, aircraft_id: str, installation_id: str) -> None:
        self.tasks.append(
            {
                "task_id": str(uuid5(NAMESPACE_URL, f"fleetiq:inspection:{installation_id}")),
                "aircraft_id": aircraft_id,
                "installation_id": installation_id,
                "template_code": "routine_inspection",
                "part_code": None,
                "quantity_required": 0,
                "queued_at": (self.cutoff - timedelta(hours=6)).isoformat(),
                "status": "queued",
                "blocked_reason": "planned_backlog",
            }
        )

    def tables(self) -> dict[str, pd.DataFrame]:
        workers = [
            {"worker_id": worker, "skill_pool": skill}
            for skill, pool in (("mechanical", self.mechanics), ("inspection", self.inspectors))
            for worker in pool
        ]
        return {
            "bays": pd.DataFrame(
                [{"bay_id": bay, "type_code": "DEMO-SINGLE"} for bay in self.bays]
            ),
            "workers": pd.DataFrame(workers),
            "task_templates": pd.DataFrame(
                [
                    {
                        "template_code": "engine_exchange",
                        "work_hours": 2,
                        "inspection_hours": 1,
                        "release_hours": 1,
                        "skill_pool": "mechanical",
                        "inspection_pool": "inspection",
                    },
                    {
                        "template_code": "routine_inspection",
                        "work_hours": 0,
                        "inspection_hours": 1,
                        "release_hours": 1,
                        "skill_pool": "inspection",
                        "inspection_pool": "inspection",
                    },
                ]
            ),
            "maintenance_tasks": pd.DataFrame(self.tasks),
            "maintenance_events": pd.DataFrame(
                self.events,
                columns=[
                    "event_id",
                    "task_id",
                    "kind",
                    "event_time",
                    "recorded_at",
                    "worker_id",
                    "bay_id",
                ],
            ),
        }
