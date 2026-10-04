"""Independent reproducible workload and environment streams."""

import hashlib
import math

import numpy as np


def stream(seed: int, serial: str, name: str) -> np.random.Generator:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    material = f"fleetiq-synthetic-v1:{seed}:{serial}:{name}".encode()
    return np.random.default_rng(int.from_bytes(hashlib.sha256(material).digest(), "big"))


def hourly_context(
    hour: int,
    workload_rng: np.random.Generator,
    environment_rng: np.random.Generator,
    *,
    workload_scale: float = 1,
    ambient_offset_c: float = 0,
) -> tuple[float, float]:
    workload = workload_scale * float(np.clip(workload_rng.normal(0.75, 0.12), 0.2, 1.2))
    ambient = 25 + ambient_offset_c + 8 * math.sin(2 * math.pi * hour / 24)
    ambient += float(environment_rng.normal(0, 1.5))
    return workload, ambient
