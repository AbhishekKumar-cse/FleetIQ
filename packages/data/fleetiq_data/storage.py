"""Content-addressed raw inputs; clients never choose a destination filename."""

import hashlib
import os
import tempfile
from pathlib import Path

FORMATS = {".csv", ".json", ".parquet"}


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def store_raw(path, *, input_root, vault_root, max_bytes=64 * 1024 * 1024):
    path, root, vault = Path(path).resolve(), Path(input_root).resolve(), Path(vault_root).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix not in FORMATS:
        raise ValueError("Input must be CSV, JSON or Parquet within the allowed data root")
    if path.stat().st_size > max_bytes:
        raise ValueError("Input exceeds batch upload size limit")
    vault.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=vault)
    try:
        with os.fdopen(fd, "wb") as output, path.open("rb") as source:
            digest, size = hashlib.sha256(), 0
            for block in iter(lambda: source.read(1024 * 1024), b""):
                size += len(block)
                if size > max_bytes:
                    raise ValueError("Input exceeds batch upload size limit")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o444)
        fingerprint = digest.hexdigest()
        destination = vault / (fingerprint + path.suffix)
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            if destination.is_symlink() or checksum(destination) != fingerprint:
                raise ValueError("Raw vault integrity violation") from None
        return destination, fingerprint
    finally:
        Path(temporary).unlink(missing_ok=True)
