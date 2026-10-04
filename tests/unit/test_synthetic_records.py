from datetime import datetime

import pandas as pd
import pytest
from fleetiq_data.synthetic.fleet import generate_fleet, load_demo_config
from fleetiq_data.synthetic.logistics import StockLedger


@pytest.fixture(scope="module")
def scenario():
    return generate_fleet(load_demo_config())


def test_fifty_fictional_assets_and_relational_integrity(scenario):
    tables = scenario.observed
    assert len(tables["aircraft"]) == 50 and tables["aircraft"].fictional.all()
    assert set(tables["installations"].aircraft_id) <= set(tables["aircraft"].aircraft_id)
    assert set(tables["installations"].component_serial) == set(
        tables["components"].component_serial
    )
    assert set(tables["maintenance_tasks"].installation_id) <= set(
        tables["installations"].installation_id
    )
    assert tables["workers"].worker_id.is_unique
    assert set(tables["workers"].skill_pool) == {"mechanical", "inspection"}
    assert tables["maintenance_tasks"].status.eq("queued").any()
    assert tables["maintenance_tasks"].blocked_reason.eq("spare_shortage").any()
    assert len(tables["confirmed_faults"]) > 0


def test_nonoverlapping_replacement_and_no_postfailure_readings(scenario):
    installs = scenario.observed["installations"]
    assert installs.installation_id.is_unique and installs.component_serial.is_unique
    for _, group in installs.groupby("aircraft_id"):
        records = group.sort_values("installed_at").to_dict("records")
        for old, new in zip(records, records[1:]):
            assert old["removed_at"] == new["installed_at"]
            assert old["component_serial"] != new["component_serial"]
    readings = scenario.observed["sensor_observations"]
    for event in scenario.evaluator["physical_events"].to_dict("records"):
        if event["status"] == "confirmed":
            part = readings.loc[readings.installation_id == event["installation_id"]]
            assert (
                pd.to_datetime(part.measured_at, utc=True) < pd.Timestamp(event["event_time"])
            ).all()
    assert not {"latent_damage", "failure", "rul"}.intersection(readings.columns)


def test_stock_never_negative_and_future_receipts_not_on_hand(scenario):
    movements = scenario.observed["stock_movements"].sort_values("event_time")
    assert (movements.quantity_delta.cumsum() >= 0).all()
    assert (pd.to_datetime(movements.recorded_at, utc=True) <= scenario.cutoff).all()
    assert scenario.observed["inbound_orders"].received_at.isna().all()
    assert (
        pd.to_datetime(scenario.evaluator["future_inbound"].announced_at, utc=True)
        > scenario.cutoff
    ).all()
    assert set(scenario.observed["part_compatibility"].type_code) == set(
        scenario.observed["aircraft"].type_code
    )
    config = load_demo_config()
    ledger = StockLedger(config.epoch, scenario.cutoff, 0, 1, 72)
    with pytest.raises(ValueError):
        ledger.issue(config.epoch, "unavailable")


def test_release_requires_inspection_and_resource_intervals_do_not_overlap(scenario):
    events = scenario.observed["maintenance_events"]
    intervals = {}
    for _, task in events.groupby("task_id"):
        by_kind = task.set_index("kind")
        assert (
            by_kind.loc["work_completed", "event_time"]
            <= by_kind.loc["inspection_started", "event_time"]
        )
        assert (
            by_kind.loc["inspection_passed", "event_time"] < by_kind.loc["released", "event_time"]
        )
        for field, first, last in (
            ("bay_id", "work_started", "released"),
            ("worker_id", "work_started", "work_completed"),
            ("worker_id", "inspection_started", "inspection_passed"),
        ):
            key = by_kind.loc[first, field]
            intervals.setdefault(key, []).append(
                (
                    datetime.fromisoformat(by_kind.loc[first, "event_time"]),
                    datetime.fromisoformat(by_kind.loc[last, "event_time"]),
                )
            )
    for periods in intervals.values():
        periods.sort()
        assert all(a[1] <= b[0] for a, b in zip(periods, periods[1:]))


def test_fixed_seed_scenario_repeats(scenario):
    repeated = generate_fleet(load_demo_config())
    for access in ("observed", "evaluator"):
        for name, frame in getattr(scenario, access).items():
            pd.testing.assert_frame_equal(frame, getattr(repeated, access)[name])
