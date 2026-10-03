"""Create demo-only secrets on native Linux storage; preserve existing material."""

import argparse
import hashlib
import json
import os
import secrets
import shlex
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]


def private_write(path: Path, content: bytes) -> None:
    if not path.exists():
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(content)
    if path.stat().st_mode & 0o077:
        raise PermissionError(
            "secret file has unsafe permissions; preserve it and repair permissions"
        )


def bootstrap(root: Path = ROOT, secret_dir: Path | None = None) -> Path:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if secret_dir is None:
        suffix = hashlib.sha256(str(root).encode()).hexdigest()[:12]
        secret_dir = Path.home() / ".local/share/fleetiq" / f"secrets-{suffix}"
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(secret_dir, 0o700)
    link = root / ".secrets"
    if link.exists() or link.is_symlink():
        if link.resolve() != secret_dir.resolve():
            raise ValueError("existing secrets location differs; refusing to replace it")
    else:
        link.symlink_to(secret_dir, target_is_directory=True)
    private = secret_dir / "auth_private.pem"
    if not private.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_write(
            private,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
    private_write(private, b"")
    key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public = secret_dir / "auth_public.pem"
    private_write(public, public_bytes)
    if public.read_bytes() != public_bytes:
        raise ValueError("existing public/private key pair does not match")
    env = secret_dir / "dev.env"
    if not env.exists():
        values = {}
        for field, user in {
            "DATABASE_URL": "app",
            "WORKER_DATABASE_URL": "worker",
            "MIGRATION_DATABASE_URL": "migration",
            "TEST_ADMIN_DATABASE_URL": "test_admin",
            "MLFLOW_DATABASE_URL": "mlflow",
        }.items():
            values[field] = (
                f"postgresql+psycopg://fleetiq_{user}:{secrets.token_urlsafe(32)}@127.0.0.1:5432/fleetiq"
            )
        values |= {
            "ARTIFACT_ROOT": str(root / "artifacts"),
            "REPORT_ROOT": str(root / "docs/exports"),
            "MODEL_BUNDLE_PATH": str(root / "artifacts/models/active"),
            "AUTH_ISSUER": "http://localhost:8000",
            "AUTH_AUDIENCE": "fleetiq-local",
            "AUTH_KEY_ID": "dev-1",
            "AUTH_PRIVATE_KEY_PATH": str(private),
            "AUTH_PUBLIC_KEY_PATH": str(public),
            "INFERENCE_WORKLOAD_TOKEN": secrets.token_urlsafe(48),
            "ALLOWED_ORIGINS": json.dumps(["http://localhost:3000"]),
            "WORKER_CONCURRENCY": "1",
            "INFERENCE_TIMEOUT_SECONDS": "10",
            "SOURCE_TRACK": "synthetic_engine_demo",
            "REPLAY_SOURCE": str(root / "data/synthetic/observed"),
        }
        private_write(env, "".join(f"{k}={shlex.quote(v)}\n" for k, v in values.items()).encode())
    private_write(env, b"")
    env_link = root / ".env"
    if env_link.exists() or env_link.is_symlink():
        if env_link.resolve() != env.resolve():
            raise ValueError("existing .env preserved; refusing to replace it")
    else:
        env_link.symlink_to(".secrets/dev.env")
    return env_link


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    bootstrap()
    print("Local demo credentials ready; existing values preserved; no secrets printed.")
