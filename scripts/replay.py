"""Bounded, disk-spooled historical fixture replay with durable acknowledgments."""

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fleetiq_data.importer import UNIT_FIELDS, parse_rows
from fleetiq_data.quality.profiles import load_profiles
from fleetiq_data.quality.time import classify_time
from fleetiq_data.quality.values import validate_value

MAX_SPOOL_BYTES = 8 * 1024 * 1024


def send_batch(client, payload, key, *, sleep=time.sleep):
    for attempt in range(6):
        try:
            response = client.post(
                "/ingestion/telemetry-batches",
                content=payload,
                headers={"Idempotency-Key": key, "Content-Type": "application/json"},
            )
        except httpx.TransportError:
            response = None
        if response is not None and response.status_code == 200:
            return response.json()
        if response is not None and response.status_code not in {429, 503}:
            raise RuntimeError("Replay rejected (HTTP " + str(response.status_code) + ")")
        if attempt == 5:
            raise RuntimeError("Replay retry budget exhausted; spool retained")
        delay = min(30, 2**attempt)
        if response is not None:
            try:
                delay = min(30, max(delay, float(response.headers.get("Retry-After", 0))))
            except ValueError:
                pass
        sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rate", type=int, default=20)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--api", required=True)
    parser.add_argument("--credential", type=Path, default=Path(".secrets/replay_source.json"))
    args = parser.parse_args()
    parsed = urlparse(args.api)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username
        or parsed.password
    ):
        parser.error("Local HTTP endpoint required for this demo source credential")
    if not 1 <= args.rate <= 100 or not 1 <= args.duration <= 3600:
        parser.error("Rate 1–100 observations/s; duration 1–3600 s")
    credential = json.loads(args.credential.read_text())
    path = args.input / "sensor_observations.parquet" if args.input.is_dir() else args.input
    profiles = load_profiles("synthetic_engine")
    rows = [
        row
        for row in parse_rows(path)
        if classify_time(
            row["measured_at"], row["recorded_at"], now=datetime.now(UTC), mode="historical"
        )
        == "eligible"
        and all(
            validate_value(
                row.get(channel),
                row.get(UNIT_FIELDS[channel]),
                profile,
                pressure_kind="absolute" if profile.pressure_kind else None,
            ).value
            is not None
            and validate_value(
                row.get(channel),
                row.get(UNIT_FIELDS[channel]),
                profile,
                pressure_kind="absolute" if profile.pressure_kind else None,
            ).quality
            != "invalid"
            for channel, profile in profiles.items()
        )
    ]
    limit = min(len(rows), args.rate * args.duration)
    spool = Path(".scratch/replay-spool/batch.json")
    spool.parent.mkdir(parents=True, exist_ok=True)
    totals = dict(observations=0, accepted=0, flagged=0, duplicate=0, readings=0, batches=0)
    started = time.monotonic()
    with httpx.Client(
        base_url=args.api, headers={"X-Source-Key": credential["token"]}, timeout=30
    ) as client:

        def flush():
            payload = spool.read_bytes()
            if len(payload) > MAX_SPOOL_BYTES:
                raise RuntimeError("Spool exceeds configured bound")
            response = send_batch(client, payload, hashlib.sha256(payload).hexdigest())
            for field in totals:
                totals[field] += 1 if field == "batches" else response[field]
            spool.unlink()

        if spool.exists():
            flush()
        batch_size = min(50, args.rate * 2)
        for start in range(0, limit, batch_size):
            payload = json.dumps(
                {"rows": rows[start : start + batch_size], "mode": "historical"}, allow_nan=False
            ).encode()
            if len(payload) > MAX_SPOOL_BYTES:
                raise RuntimeError("Spool exceeds configured bound")
            temporary = spool.with_suffix(".tmp")
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(spool)
            flush()
            delay = started + min(start + batch_size, limit) / args.rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    print(
        json.dumps(
            {
                "mode": "historical_fixture_replay",
                "elapsed_seconds": round(time.monotonic() - started, 2),
                **totals,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
