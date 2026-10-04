"""Profile verified source bytes and persist observed trajectories separately."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from fleetiq_data.cmapss import parse_evaluator_rul, parse_trajectory
from fleetiq_data.fetch import ROOT


def summarize(frame: pd.DataFrame) -> dict:
    grouped = frame.groupby("unit_id", sort=True)
    return {
        "rows": len(frame),
        "engines": frame.unit_id.nunique(),
        "per_engine": [
            {
                "unit_id": int(unit),
                "rows": len(part),
                "first_cycle": int(part.cycle.min()),
                "last_cycle": int(part.cycle.max()),
                "cycle_gaps": int((part.cycle.diff().dropna() != 1).sum()),
            }
            for unit, part in grouped
        ],
        "null_counts": {name: int(value) for name, value in frame.isna().sum().items()},
        "duplicates": int(frame.duplicated(["unit_id", "cycle"]).sum()),
    }


def profile(input_path: Path, subset: str, output: Path) -> dict:
    manifest = json.loads((input_path / "manifest.json").read_text())
    names = [f"train_{subset}.txt", f"test_{subset}.txt", f"RUL_{subset}.txt"]
    for name in names:
        expected = manifest["files"][name]
        # Fetch manifests store per-file SHA-256 strings.
        actual = hashlib.sha256((input_path / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"source checksum mismatch: {name}")
    train = parse_trajectory(input_path / names[0])
    test = parse_trajectory(input_path / names[1])
    targets = parse_evaluator_rul(input_path / names[2], sorted(test.unit_id.unique().tolist()))
    result = {
        "schema_version": "cmapss-profile-v1",
        "subset": subset,
        "life_unit": "cycles",
        "calendar": "not_provided",
        "source_manifest": manifest,
        "train": summarize(train),
        "test_observed": summarize(test),
        "constant_training_sensors": [
            name
            for name in train.columns
            if name.startswith("sensor_") and train[name].nunique() == 1
        ],
        "terminal_target_alignment": {
            "engines": len(targets),
            "aligned": True,
            "access": "evaluator_only",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    train.to_parquet(output.parent / "train.parquet", index=False, compression="zstd")
    test.to_parquet(output.parent / "test_observed.parquet", index=False, compression="zstd")
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--subset", choices=[f"FD00{i}" for i in range(1, 5)], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.input.resolve().is_relative_to(ROOT / "data" / "raw"):
        parser.error("input must be under data/raw")
    if not args.output.resolve().is_relative_to(ROOT / "data" / "processed"):
        parser.error("output must be under data/processed")
    result = profile(args.input, args.subset, args.output)
    print(
        f"Profiled {result['train']['rows']} training and {result['test_observed']['rows']} test rows"
    )


if __name__ == "__main__":
    main()
