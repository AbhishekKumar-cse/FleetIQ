import time

from fastapi.testclient import TestClient
from fleetiq_inference.serving import create_app

TOKEN = "test-only-private-workload-token-12345"


class FakeBundle:
    hash = "a" * 64

    def __init__(self, folder, digest):
        if digest != self.hash:
            raise ValueError("Damaged bundle")

    def predict(self, request):
        if request["schema_hash"] != "b" * 64 or request["horizon"] != 30:
            raise ValueError("Wrong contract")
        if request["request_id"] == "slow":
            time.sleep(0.05)
        return dict(request_id=request["request_id"], coverage="unsupported", output=None)


def payload():
    return dict(
        requests=[
            dict(
                request_id="one",
                task="failure_risk",
                track="cmapss_benchmark",
                unit="cycles",
                horizon=30,
                native_cycle=30,
                names=["a"],
                values=[1.0],
                schema_hash="b" * 64,
                supported=True,
            )
        ]
    )


def test_private_auth_schema_horizon_batch_and_explicit_abstention():
    with TestClient(
        create_app(folder="fixture", approved_hash="a" * 64, token=TOKEN, bundle_factory=FakeBundle)
    ) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.post("/internal/v1/predict", json=payload()).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        result = client.post("/internal/v1/predict", json=payload(), headers=headers)
        assert result.status_code == 200 and result.json()["predictions"][0]["output"] is None
        wrong = payload()
        wrong["requests"][0]["schema_hash"] = "c" * 64
        assert client.post("/internal/v1/predict", json=wrong, headers=headers).status_code == 422
        wrong = payload()
        wrong["requests"][0]["horizon"] = 24
        assert client.post("/internal/v1/predict", json=wrong, headers=headers).status_code == 422
        assert (
            client.post(
                "/internal/v1/predict",
                json={"requests": payload()["requests"] * 33},
                headers=headers,
            ).status_code
            == 422
        )
        assert client.get("/docs").status_code == 404


def test_damaged_bundle_is_not_ready_and_timeout_is_explicit():
    with TestClient(
        create_app(folder="fixture", approved_hash="bad", token=TOKEN, bundle_factory=FakeBundle)
    ) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
    with TestClient(
        create_app(
            folder="fixture",
            approved_hash="a" * 64,
            token=TOKEN,
            timeout=0.001,
            bundle_factory=FakeBundle,
        )
    ) as client:
        slow = payload()
        slow["requests"][0]["request_id"] = "slow"
        assert (
            client.post(
                "/internal/v1/predict", json=slow, headers={"Authorization": "Bearer " + TOKEN}
            ).status_code
            == 504
        )
