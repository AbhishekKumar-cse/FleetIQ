"""Build bounded fictional histories; no readings after failure or across replacement."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from fleetiq_data.fetch import ROOT
from fleetiq_data.synthetic.degradation import generate_trajectory
from fleetiq_data.synthetic.failures import sample_failures
from fleetiq_data.synthetic.logistics import StockLedger
from fleetiq_data.synthetic.maintenance import MaintenanceHistory


class DemoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "fictional-fleet-v1"
    epoch: datetime
    hours: int = Field(ge=24, le=8760)
    aircraft_count: int = Field(ge=1, le=1000)
    seed: int = Field(ge=0)
    engine_bays: int = Field(ge=1)
    mechanics: int = Field(ge=1)
    inspectors: int = Field(ge=1)
    initial_engine_stock: int = Field(ge=0)
    confirmed_receipt_quantity: int = Field(ge=0)
    confirmed_receipt_hour: int = Field(ge=0)
    assumptions: list[str]

    @field_validator("epoch")
    @classmethod
    def aware_epoch(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scenario epoch must be timezone-aware")
        if value.minute or value.second or value.microsecond:
            raise ValueError("scenario epoch must be an hour boundary")
        return value


def load_demo_config(path=ROOT / "config/demo.yaml") -> DemoConfig:
    return DemoConfig.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class FleetScenario:
    observed: dict[str, pd.DataFrame]
    evaluator: dict[str, pd.DataFrame]
    cutoff: datetime


def generate_fleet(config: DemoConfig, seed: int | None = None) -> FleetScenario:
    seed = config.seed if seed is None else seed
    cutoff = config.epoch + timedelta(hours=config.hours)
    stock = StockLedger(
        config.epoch,
        cutoff,
        config.initial_engine_stock,
        config.confirmed_receipt_quantity,
        config.confirmed_receipt_hour,
    )
    maintenance = MaintenanceHistory(
        config.epoch, cutoff, stock, config.engine_bays, config.mechanics, config.inspectors
    )
    aircraft, components, installations, observations, latent, event_truth, known_faults, usage = (
        [] for _ in range(8)
    )
    for index in range(config.aircraft_count):
        aircraft_id = str(uuid5(NAMESPACE_URL, f"fleetiq:demo:{seed}:aircraft:{index}"))
        aircraft.append(
            {
                "aircraft_id": aircraft_id,
                "tail_label": f"DEMO-{index + 1:03d}",
                "type_code": "DEMO-SINGLE",
                "site_code": "DEMO-BASE",
                "fleet_code": "DEMO-FLEET",
                "fictional": True,
            }
        )
        start, exchange = config.epoch, 0
        while start < cutoff:
            remaining = int((cutoff - start).total_seconds() // 3600)
            if remaining < 1:
                break
            serial = f"FICTIONAL-{index + 1:03d}-{exchange:03d}"
            regime = "ood" if index % 10 == 0 else "degrading" if index % 3 == 0 else "normal"
            trajectory = generate_trajectory(
                seed=seed,
                hours=remaining,
                component_serial=serial,
                installed_at=start,
                aircraft_id=UUID(aircraft_id),
                regime=regime,
                initial_age_hours=400 if exchange == 0 else 0,
            )
            failure = sample_failures(trajectory.evaluator, seed=seed).iloc[0].to_dict()
            event_truth.append(failure)
            installation = trajectory.installations.iloc[0].to_dict()
            installation_id = installation["installation_id"]
            components.append(
                {"component_serial": serial, "part_code": "DEMO-ENGINE", "fictional": True}
            )
            event = datetime.fromisoformat(failure["event_time"]) if failure["event_time"] else None
            active_end = event or cutoff
            mask = pd.to_datetime(trajectory.observed.measured_at, utc=True) < active_end
            observations.append(trajectory.observed.loc[mask])
            latent.append(
                trajectory.evaluator.loc[
                    pd.to_datetime(trajectory.evaluator.measured_at, utc=True) <= active_end
                ]
            )
            duration = int((active_end - start).total_seconds() // 3600)
            if duration:
                usage.append(
                    {
                        "installation_id": installation_id,
                        "aircraft_id": aircraft_id,
                        "start": start.isoformat(),
                        "end": (start + timedelta(hours=duration)).isoformat(),
                        "operating_hours": duration,
                        "flight_cycles": duration,
                        "recorded_at": (start + timedelta(hours=duration)).isoformat(),
                    }
                )
            replacement = None
            if event:
                confirmation = datetime.fromisoformat(failure["recorded_at"])
                if confirmation <= cutoff:
                    known_faults.append(
                        {key: value for key, value in failure.items() if key != "truth_access"}
                    )
                    replacement = maintenance.exchange(aircraft_id, installation_id, confirmation)
            elif index % 7 == 0:
                maintenance.inspection_backlog(aircraft_id, installation_id)
            installation["removed_at"] = replacement.isoformat() if replacement else None
            installations.append(installation)
            if replacement is None:
                break
            start, exchange = replacement, exchange + 1
    observed = {
        "aircraft": pd.DataFrame(aircraft),
        "components": pd.DataFrame(components),
        "installations": pd.DataFrame(installations),
        "sensor_observations": pd.concat(observations, ignore_index=True),
        "usage": pd.DataFrame(usage),
        "confirmed_faults": pd.DataFrame(
            known_faults, columns=[key for key in event_truth[0] if key != "truth_access"]
        ),
    }
    observed.update(maintenance.tables())
    observed.update(stock.tables())
    evaluator = {
        "latent_health": pd.concat(latent, ignore_index=True),
        "physical_events": pd.DataFrame(event_truth),
        "future_inbound": pd.DataFrame(stock.future),
    }
    return FleetScenario(observed, evaluator, cutoff)
