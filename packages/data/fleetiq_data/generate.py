"""Generate traceable fictional records and separated evaluator truth."""

import argparse
import importlib.metadata
import io
import json
import platform
from pathlib import Path

from fleetiq_data.fetch import ROOT
from fleetiq_data.manifest import canonical_json, guarded_output, persist_dataset, sha256
from fleetiq_data.synthetic.fleet import DemoConfig, generate_fleet, load_demo_config
from fleetiq_data.synthetic.quality import inject_quality


def generate_dataset(config: DemoConfig, seed: int, output: Path, root: Path = ROOT) -> dict:
    guarded_output(output, root)
    scenario = generate_fleet(config, seed)
    quality = inject_quality(scenario.observed["sensor_observations"], seed=seed)
    scenario.observed["sensor_observations"] = quality.observed
    scenario.evaluator["measurement_faults"] = quality.evaluator_faults
    for frame in scenario.observed.values():
        if {"latent_damage", "truth_access", "rul_cycles", "failure_within_horizon"}.intersection(
            frame.columns
        ):
            raise ValueError("evaluator columns found in observed tables")
    files, profile = {}, {}
    for access, tables in (("observed", scenario.observed), ("evaluator", scenario.evaluator)):
        for name, frame in sorted(tables.items()):
            buffer = io.BytesIO()
            frame.to_parquet(buffer, index=False, compression="zstd")
            files[f"{access}/{name}.parquet"] = buffer.getvalue()
            profile[f"{access}/{name}"] = {
                "rows": len(frame),
                "columns": frame.columns.tolist(),
                "missing_cells": int(frame.isna().sum().sum()),
            }
    files["evaluator/profile.json"] = canonical_json(profile)
    source = json.loads((ROOT / "data/raw/cmapss/manifest.json").read_text())
    inputs = [
        ROOT / "config" / name
        for name in (
            "synthetic.yaml",
            "targets.json",
            "data_sources.json",
            "cmapss.schema.json",
            "synthetic.schema.json",
        )
    ]
    inputs += sorted((ROOT / "packages/data/fleetiq_data").rglob("*.py"))
    metadata = {
        "manifest_version": "dataset-manifest-v1",
        "generator_version": "fictional-fleet-v1",
        "track": "synthetic_engine_demo",
        "seed": seed,
        "scenario": config.model_dump(mode="json") | {"seed": seed},
        "cutoff": scenario.cutoff.isoformat(),
        "schema_version": "synthetic-engine-v1",
        "input_sha256": {str(path.relative_to(ROOT)): sha256(path.read_bytes()) for path in inputs},
        "benchmark_provenance": {
            key: source[key]
            for key in ("archive_sha256", "download_url", "catalog_url", "license_review")
        },
        "runtime": {
            "python": platform.python_version(),
            **{
                name: importlib.metadata.version(name)
                for name in ("numpy", "pandas", "pyarrow", "pydantic")
            },
        },
        "access_terms": "fictional local prototype; NASA distribution rights pending review",
        "limitations": config.assumptions
        + [
            "Raw sensor faults require validation/quarantine",
            "No trained model or certified physical behaviour is claimed",
        ],
        "split_policy": "distinct source tracks; engine/time training splits are assigned in later steps",
    }
    return persist_dataset(output, files, metadata, root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.config.resolve().is_relative_to(ROOT / "config"):
        parser.error("config must stay under config/")
    try:
        result = generate_dataset(load_demo_config(args.config), args.seed, args.output)
    except ValueError as error:
        parser.error(str(error))
    print(
        f"Generated {len(result['files'])} separated dataset files; content SHA-256 {result['content_sha256']}"
    )


if __name__ == "__main__":
    main()
