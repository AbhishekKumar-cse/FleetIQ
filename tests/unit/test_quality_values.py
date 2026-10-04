from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_data.quality.profiles import Profile, load_profiles
from fleetiq_data.quality.values import causal_fill, essential_supported, validate_value

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("raw", ["bad", "1e9999", float("nan"), float("inf"), True, {}])
def test_invalid_preserves_raw(raw):
    p = load_profiles("synthetic_engine")["temperature_c"]
    r = validate_value(raw, "degC", p)
    assert r.value is None and r.quality == "invalid"
    assert r.raw_value is raw


def test_units_ranges_and_anomalies():
    profiles = load_profiles("synthetic_engine")
    t = profiles["temperature_c"]
    assert validate_value(300, "K", t).value == pytest.approx(26.85)
    assert validate_value(68, "degF", t).value == pytest.approx(20)
    assert validate_value(200, "degC", t).quality == "flagged"
    assert validate_value(200, "degC", t).value == 200
    assert validate_value(900, "degC", t).quality == "invalid"
    for unit in [None, "unknown", "kPa"]:
        assert validate_value(20, unit, t).quality == "invalid"
    absolute = profiles["oil_pressure_kpa"]
    assert validate_value(-1, "kPa", absolute, pressure_kind="absolute").quality == "invalid"
    assert validate_value(4, "bar", absolute, pressure_kind="absolute").value == 400
    assert validate_value(400, "kPa", absolute).quality == "invalid"
    assert validate_value(400, "kPa", absolute, pressure_kind="gauge").quality == "invalid"
    gauge = load_profiles("synthetic_gauge_fixture")["pressure"]
    assert validate_value(-20, "kPa", gauge, pressure_kind="gauge").value == -20


def test_essential_and_bounded_causal_fill():
    profiles = load_profiles("synthetic_engine")
    t = profiles["temperature_c"]
    values = {
        k: validate_value(
            300 if k == "oil_pressure_kpa" else 1, p.unit, p, pressure_kind=p.pressure_kind
        )
        for k, p in profiles.items()
    }
    assert essential_supported(values, profiles)
    values.pop("vibration_mm_s")
    assert not essential_supported(values, profiles)
    assert validate_value(None, t.unit, t).quality == "missing"
    optional = Profile("degC", False, (-80, 600), (-40, 150), 60, 20)
    assert essential_supported({}, {"optional": optional})
    now = datetime(2026, 1, 1, tzinfo=UTC)
    previous = validate_value(20, t.unit, t)
    assert causal_fill(
        previous, now, now + timedelta(seconds=60), now + timedelta(minutes=2), t
    ) == (20, True)
    for at, as_of in [
        (now + timedelta(seconds=61), now + timedelta(minutes=2)),
        (now - timedelta(seconds=1), now),
        (now + timedelta(seconds=1), now),
    ]:
        assert causal_fill(previous, now, at, as_of, t) == (None, False)


def test_unreviewed_source_and_invalid_profile_rejected():
    with pytest.raises(ValueError):
        load_profiles("nasa_cmapss")
    with pytest.raises(ValueError):
        Profile("kPa", True, (-1, 100), (0, 10), 60, 20, "absolute")
