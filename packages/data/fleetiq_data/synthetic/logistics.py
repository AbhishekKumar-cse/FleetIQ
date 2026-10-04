"""Compatible spares with recorded receipts, issues, and separated future truth."""

from datetime import datetime, timedelta

import pandas as pd


class StockLedger:
    def __init__(
        self, epoch: datetime, cutoff: datetime, initial: int, receipt: int, receipt_hour: int
    ):
        if min(initial, receipt, receipt_hour) < 0:
            raise ValueError("stock quantities and receipt hour must be nonnegative")
        self.cutoff = cutoff
        self.movements = [
            {
                "part_code": "DEMO-ENGINE",
                "quantity_delta": initial,
                "event_time": epoch.isoformat(),
                "recorded_at": epoch.isoformat(),
                "kind": "opening_balance",
                "task_id": None,
            }
        ]
        received_at = epoch + timedelta(hours=receipt_hour)
        if received_at <= cutoff:
            self.movements.append(
                {
                    "part_code": "DEMO-ENGINE",
                    "quantity_delta": receipt,
                    "event_time": received_at.isoformat(),
                    "recorded_at": received_at.isoformat(),
                    "kind": "receipt",
                    "task_id": None,
                }
            )
        self.inbound = [
            {
                "order_id": "DEMO-ANNOUNCED-001",
                "part_code": "DEMO-ENGINE",
                "quantity": 3,
                "announced_at": cutoff.isoformat(),
                "expected_at": (cutoff + timedelta(hours=48)).isoformat(),
                "received_at": None,
                "status": "announced",
            }
        ]
        self.future = [
            {
                "order_id": "DEMO-FUTURE-001",
                "part_code": "DEMO-ENGINE",
                "quantity": 5,
                "announced_at": (cutoff + timedelta(hours=24)).isoformat(),
                "expected_at": (cutoff + timedelta(hours=96)).isoformat(),
                "truth_access": "evaluator_only",
            }
        ]

    def available(self, at: datetime) -> int:
        # Existing reservations are subtracted regardless of issue time, preventing double allocation.
        receipts = sum(
            m["quantity_delta"]
            for m in self.movements
            if m["quantity_delta"] >= 0 and datetime.fromisoformat(m["recorded_at"]) <= at
        )
        issues = sum(m["quantity_delta"] for m in self.movements if m["quantity_delta"] < 0)
        return max(0, receipts + issues)

    def issue(self, at: datetime, task_id: str) -> None:
        if at > self.cutoff or self.available(at) < 1:
            raise ValueError("cannot issue unavailable or future stock")
        self.movements.append(
            {
                "part_code": "DEMO-ENGINE",
                "quantity_delta": -1,
                "event_time": at.isoformat(),
                "recorded_at": at.isoformat(),
                "kind": "issue",
                "task_id": task_id,
            }
        )

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "parts": pd.DataFrame(
                [{"part_code": "DEMO-ENGINE", "name": "Fictional exchange engine"}]
            ),
            "part_compatibility": pd.DataFrame(
                [{"part_code": "DEMO-ENGINE", "type_code": "DEMO-SINGLE"}]
            ),
            "stock_movements": pd.DataFrame(self.movements)
            .sort_values("event_time")
            .reset_index(drop=True),
            "inbound_orders": pd.DataFrame(self.inbound),
        }
