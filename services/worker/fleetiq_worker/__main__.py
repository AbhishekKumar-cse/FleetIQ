"""Independent bounded worker with fenced completion and graceful draining."""

import argparse
import json
import signal
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from uuid import uuid4

import sqlalchemy as sa
from fleetiq_api.settings import Settings

from fleetiq_worker.handlers import dispatch
from fleetiq_worker.jobs import LeaseLost, claim, fail, finish, heartbeat

HEARTBEAT_SECONDS = 20


def execute(engine, lease):
    stopped, lost = threading.Event(), threading.Event()

    def renew():
        while not stopped.wait(HEARTBEAT_SECONDS):
            try:
                with engine.begin() as c:
                    heartbeat(c, lease)
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=renew, name="lease-heartbeat", daemon=True)
    thread.start()
    try:
        if lease.job["kind"] == "dataset.import":
            from fleetiq_worker.dataset_handler import import_dataset

            outcome = import_dataset(engine, lease)
        elif lease.job["kind"] in {"prediction.compute", "prediction.explain"}:
            from fleetiq_worker.prediction_handler import compute, explain

            outcome = (compute if lease.job["kind"] == "prediction.compute" else explain)(
                engine, lease
            )
        else:
            with engine.connect() as c:
                outcome = dispatch(c, lease.job)
        if lost.is_set():
            return "lease_lost"
        with engine.begin() as c:
            finish(c, lease, outcome.result, unsupported=outcome.unsupported, effect=outcome.effect)
        return "unsupported" if outcome.unsupported else "completed"
    except LeaseLost:
        return "lease_lost"
    except Exception as error:
        try:
            with engine.begin() as c:
                fail(c, lease, "Handler failed (" + type(error).__name__ + ")")
            return "retry_or_dead_letter"
        except LeaseLost:
            return "lease_lost"
    finally:
        stopped.set()
        thread.join(timeout=5)


def run(engine, *, concurrency=2, once=False, stop=None):
    if concurrency not in (1, 2):
        raise ValueError("Worker concurrency is bounded to one or two")
    stop = stop or threading.Event()
    owner = "worker-" + uuid4().hex
    counts, futures = {}, set()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="fleet-job") as pool:
        while not stop.is_set() or futures:
            empty = False
            while not stop.is_set() and len(futures) < concurrency:
                with engine.begin() as c:
                    lease = claim(c, owner)
                if lease is None:
                    empty = True
                    break
                futures.add(pool.submit(execute, engine, lease))
            if not futures:
                if once and empty or stop.is_set():
                    break
                stop.wait(1)
                continue
            done, futures = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                state = future.result()
                counts[state] = counts.get(state, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Drain immediately ready jobs, then exit"
    )
    parser.add_argument("--concurrency", type=int, choices=[1, 2])
    args = parser.parse_args()
    settings, stop = Settings(), threading.Event()
    concurrency = args.concurrency or min(settings.worker_concurrency, 2)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    engine = sa.create_engine(
        settings.worker_database_url.get_secret_value(),
        hide_parameters=True,
        pool_size=4,
        max_overflow=0,
    )
    try:
        print(
            json.dumps(
                run(engine, concurrency=concurrency, once=args.once, stop=stop), sort_keys=True
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
