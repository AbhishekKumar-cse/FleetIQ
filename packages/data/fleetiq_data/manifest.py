"""Content-addressed, immutable dataset writes with bounded storage roots."""

import hashlib
import json
from pathlib import Path

from fleetiq_data.fetch import ROOT


def canonical_json(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def guarded_output(output: Path, root: Path = ROOT) -> Path:
    root = root.resolve()
    output = output.absolute()
    allowed = root / "data" / "synthetic"
    if not output.is_relative_to(allowed) or not output.resolve().is_relative_to(allowed):
        raise ValueError("synthetic output must stay under data/synthetic")
    for parent in (output, *output.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("symlinked dataset output is forbidden")
    return output


def persist_dataset(
    output: Path, files: dict[str, bytes], metadata: dict, root: Path = ROOT
) -> dict:
    output = guarded_output(output, root)
    for name in files:
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.parts[0] not in {"observed", "evaluator"}
        ):
            raise ValueError("dataset files must have observed/evaluator access boundaries")
    manifest = metadata | {
        "files": {name: sha256(content) for name, content in sorted(files.items())}
    }
    manifest["content_sha256"] = sha256(canonical_json(manifest))
    payloads = files | {"manifest.json": canonical_json(manifest)}
    # Preflight the entire write set before creating or modifying any dataset bytes.
    for name, content in payloads.items():
        destination = output / name
        if destination.is_symlink() or any(
            p.is_symlink() for p in destination.parents if p != root
        ):
            raise ValueError("dataset symlinks are forbidden")
        if destination.exists() and destination.read_bytes() != content:
            raise ValueError("existing dataset differs; use a new isolated output directory")
    for name, content in payloads.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with destination.open("xb") as handle:
                handle.write(content)
    return manifest
