"""Training-file-only benchmark snapshots using the same callable as online serving."""

import argparse
import json
from pathlib import Path

import pandas as pd
from fleetiq_data.eda import training_group

from fleetiq_features.pipeline import SnapshotInput, expected_units, fit_pipeline, snapshot
from fleetiq_features.schema import Sample, track_schema


def benchmark_inputs(frame):
    required = [
        "unit_id",
        "cycle",
        *[f"setting_{i}" for i in range(1, 4)],
        *track_schema("cmapss_benchmark")["channels"],
    ]
    if set(frame.columns) != set(required) or frame[["unit_id", "cycle"]].duplicated().any():
        raise ValueError("Observed benchmark columns/unique keys required; targets forbidden")
    if ((frame[["unit_id", "cycle"]] < 1) | (frame[["unit_id", "cycle"]] % 1 != 0)).any().any():
        raise ValueError("Native positive integer keys required")
    if frame[["unit_id", "cycle"]].isna().any().any():
        raise ValueError("Missing native keys")
    for unit, group in frame.groupby("unit_id", sort=True):
        stream = f"NASA:FD001:train:{int(unit)}"
        sensors = {name: [] for name in track_schema("cmapss_benchmark")["channels"]}
        settings = {f"setting_{i}": [] for i in range(1, 4)}
        for row in group.sort_values("cycle").to_dict("records"):
            cycle = int(row["cycle"])
            for name, history in (sensors | settings).items():
                value = row[name]
                sample = Sample(
                    cycle,
                    None if pd.isna(value) else float(value),
                    cycle,
                    "missing" if pd.isna(value) else "valid",
                    stream=stream,
                )
                history.append(sample)
                while history and history[0].at <= cycle - 30:
                    history.pop(0)
            yield SnapshotInput(
                "cmapss_benchmark",
                stream,
                str(int(unit)),
                cycle,
                {name: tuple(rows) for name, rows in sensors.items()},
                expected_units("cmapss_benchmark"),
                settings={name: tuple(rows) for name, rows in settings.items()},
            )


def export(frame, output):
    output.mkdir(parents=True, exist_ok=True)
    # Fitting sees only the whole engines previously approved for training-only EDA.
    training = frame[frame.unit_id.map(lambda value: training_group(str(int(value))))]
    fit = fit_pipeline(benchmark_inputs(training), track="cmapss_benchmark")
    fit.save(output / "preprocessing.json")
    import pyarrow as pa
    import pyarrow.parquet as pq

    writer, rows, count = None, [], 0
    try:
        for spec in benchmark_inputs(frame):
            value = snapshot(spec, fit)
            rows.append(
                dict(
                    unit_id=int(spec.group),
                    cycle=spec.as_of,
                    stream=spec.stream,
                    input_hash=value["input_hash"],
                    schema_hash=value["schema_hash"],
                    fit_hash=value["fit_hash"],
                    supported=value["supported"],
                    masks_json=json.dumps(value["masks"]),
                    coverage_json=json.dumps(value["observed_coverage"]),
                    imputed_json=json.dumps(value["imputed_features"]),
                    **dict(zip(value["names"], value["vector"], strict=True)),
                )
            )
            count += 1
            if len(rows) >= 512:
                table = pa.Table.from_pylist(rows)
                if writer is None:
                    writer = pq.ParquetWriter(
                        output / "snapshots.parquet", table.schema, compression="zstd"
                    )
                writer.write_table(table)
                rows.clear()
        if rows:
            table = pa.Table.from_pylist(rows)
            if writer is None:
                writer = pq.ParquetWriter(
                    output / "snapshots.parquet", table.schema, compression="zstd"
                )
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    manifest = dict(
        track=fit.track,
        rows=count,
        training_groups=list(fit.training_groups),
        schema_hash=fit.schema_hash,
        fit_hash=fit.content_hash,
        input_hash=fit.training_input_hash,
        official_test_accessed=False,
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", required=True, choices=["cmapss_benchmark"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    approved = Path("data/processed/cmapss/train.parquet").resolve()
    path = args.input / "train.parquet"
    if path.is_symlink() or path.resolve() != approved:
        raise ValueError("CLI fits only the approved train file; official test is excluded")
    print(json.dumps(export(pd.read_parquet(path), args.output), sort_keys=True))


if __name__ == "__main__":
    main()
