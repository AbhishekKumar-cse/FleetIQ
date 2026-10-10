from types import SimpleNamespace

import httpx
import pytest
from fleetiq_worker.prediction_handler import InferenceClient, prediction_input_hash
from pydantic import SecretStr


def test_circuit_breaker_stops_repeated_outage_and_recovers():
    calls, clock = [], [0.0]
    settings = SimpleNamespace(
        inference_url="http://test",
        inference_timeout_seconds=1,
        inference_workload_token=SecretStr("fixture-token-" * 3),
        model_bundle_sha256="a" * 64,
    )

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503)

    client = InferenceClient(
        settings, transport=httpx.MockTransport(unavailable), clock=lambda: clock[0]
    )
    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            client.predict({})
    with pytest.raises(RuntimeError, match="circuit"):
        client.predict({})
    assert len(calls) == 3
    clock[0] = 31
    with pytest.raises(httpx.HTTPStatusError):
        client.predict({})
    assert len(calls) == 4


def test_input_identity_survives_database_numeric_normalization():
    assert prediction_input_hash({"nested": [1.0, -0.0, 0.125]}) == prediction_input_hash(
        {"nested": [1, 0, 0.125]}
    )
    assert prediction_input_hash({"nested": [1.1]}) != prediction_input_hash({"nested": [1.2]})
