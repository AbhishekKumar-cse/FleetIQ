"""Locally built native bundles anchored by an externally approved manifest digest."""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from fleetiq_evaluation.splits import ROOT, content_hash
from fleetiq_training.anomaly import forest_score, load_anomaly
from fleetiq_training.classification import experiment, file_hash, write_json
from fleetiq_training.improvement import load_component
from fleetiq_training.rul_baseline import load_baseline, load_rul, predict_rul
from fleetiq_training.rul_intervals import interval_prediction


def schema_hash(names, track, unit, version):
    return content_hash(dict(names=names, track=track, unit=unit, version=version))


def verify_files(folder, manifest, expected_hash):
    if not expected_hash or file_hash(folder / "manifest.json") != expected_hash:
        raise ValueError("Bundle manifest is not the externally approved digest")
    for name, digest in manifest["files"].items():
        path = folder / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.resolve() != path.absolute()
        ):
            raise ValueError("Untrusted bundle file path")
        if path.suffix not in {".json", ".ubj"} or file_hash(path) != digest:
            raise ValueError("Bundle file checksum/type mismatch")


class Bundle:
    def __init__(self, folder, expected_hash):
        self.folder = Path(folder).resolve()
        self.manifest = json.loads((self.folder / "manifest.json").read_text())
        verify_files(self.folder, self.manifest, expected_hash)
        self.hash = expected_hash
        if self.manifest["version"] != "fleetiq-bundle-v1":
            raise ValueError("Unknown inference contract version")
        self.components = {}
        for name, weight in self.manifest["failure"]["weights"].items():
            if weight > 0:
                names, predict = load_component(self.folder / "failure" / name, threads=2)
                if names != self.manifest["tasks"]["failure_risk"]["names"]:
                    raise ValueError("Component schema differs from bundle")
                self.components[name] = predict
        self.anomaly = load_anomaly(self.folder / "anomaly")["artifact"]
        self.rul = load_baseline(self.folder / "rul")

    def predict(self, request):
        task = request["task"]
        if task not in self.manifest["tasks"]:
            raise ValueError("Unknown prediction task")
        spec = self.manifest["tasks"][task]
        identity = dict(
            task=task,
            track=request["track"],
            unit=request["unit"],
            horizon=request["horizon"],
            bundle_hash=self.hash,
            model_version=spec["version"],
            feature_version=spec["feature_version"],
            calibration_version="display_disabled",
            request_id=request["request_id"],
        )
        if request["unit"] != spec["unit"] or request["horizon"] != spec["horizon"]:
            raise ValueError("Wrong native unit or unsupported horizon")
        if request["track"] != spec["track"]:
            return identity | dict(
                coverage="unsupported",
                output=None,
                uncertainty=None,
                explanation_status="unsupported",
                reason="task_not_validated_for_track",
            )
        if request["names"] != spec["names"] or request["schema_hash"] != spec["schema_hash"]:
            raise ValueError("Ordered feature schema mismatch")
        if not request["supported"]:
            return identity | dict(
                coverage="unsupported",
                output=None,
                uncertainty=None,
                explanation_status="unsupported",
                reason="essential_quality_or_history_unsupported",
            )
        x = np.asarray(request["values"], dtype=float)
        if x.shape != (len(spec["names"]),) or not np.isfinite(x).all():
            raise ValueError("Finite ordered feature vector required")
        output, uncertainty = None, None
        if task == "failure_risk":
            score = sum(
                float(predict(x[None])[0]) * self.manifest["failure"]["weights"][name]
                for name, predict in self.components.items()
            )
            output = dict(
                raw_score=score,
                threshold=self.manifest["failure"]["threshold"],
                review=score >= self.manifest["failure"]["threshold"],
                calibrated_probability=None,
            )
        elif task == "rul":
            raw = float(predict_rul(self.folder / "rul", x[None], [request["native_cycle"]])[0])
            output = dict(raw_cycles=raw, remaining_cycles=max(0, raw), life_unit="cycles")
            uncertainty = interval_prediction(
                self.manifest["rul_intervals"], raw, shifted=request.get("ood", False)
            )
        else:
            score = float(forest_score(self.anomaly["forest"], x[None])[0])
            output = dict(
                raw_score=score, anomaly=score > self.anomaly["threshold"], failure_probability=None
            )
        return identity | dict(
            coverage="full",
            output=output,
            uncertainty=uncertainty,
            explanation_status="pending",
            reason=None,
        )


def build_bundle(cfg, output):
    output = output.resolve()
    if not output.is_relative_to((ROOT / "artifacts/bundles").resolve()) or output.exists():
        raise ValueError("Build requires a new immutable directory under artifacts/bundles")
    output.mkdir(parents=True)
    failure_root = ROOT / "artifacts/failure_improvement_v1"
    selection = json.loads((failure_root / "selection.json").read_text())
    if (
        content_hash({k: v for k, v in selection.items() if k != "selection_hash"})
        != selection["selection_hash"]
    ):
        raise ValueError("Failure selection integrity mismatch")
    candidate = selection["candidate"]
    if candidate["kind"] != "blend":
        raise ValueError("Only frozen soft-voting selection supported")
    weights = dict(zip(candidate["components"], candidate["weights"], strict=True))
    names = None
    for name, weight in weights.items():
        if weight > 0:
            component_names, _ = load_component(failure_root / name, threads=2)
            if names is not None and names != component_names:
                raise ValueError("Different ensemble feature schemas")
            names = component_names
            shutil.copytree(failure_root / name, output / "failure" / name)
    shutil.copytree(ROOT / "artifacts/anomaly", output / "anomaly")
    rul_selection = json.loads((ROOT / "artifacts/xgb_rul/selection.json").read_text())
    shutil.copytree(ROOT / rul_selection["model_folder"], output / "rul")
    intervals = json.loads((ROOT / "artifacts/rul_calibrated/intervals.json").read_text())
    if load_baseline(output / "rul")["bundle_hash"] != intervals["model_bundle_hash"]:
        raise ValueError("RUL calibration/model binding mismatch")
    shutil.copyfile(
        ROOT / "data/processed/features/preprocessing.json", output / "rul/preprocessing.json"
    )
    data, pre, _ = load_rul(cfg, roles=("fit",))
    background = (
        data["fit"]
        .iloc[np.linspace(0, len(data["fit"]) - 1, 32, dtype=int)][list(pre.names)]
        .to_numpy()
    )
    write_json(output / "rul/background.json", dict(role="fit_only", values=background.tolist()))
    tasks = {}
    for task, track, unit, horizon, feature_names, version in (
        ("failure_risk", "cmapss_benchmark", "cycles", 30, names, "failure-improvement-v1"),
        ("rul", "cmapss_benchmark", "cycles", 0, list(pre.names), "native-cycle-rul-v1"),
        (
            "anomaly",
            "synthetic_sensor_model",
            "score",
            0,
            [
                f"{stat}.{sensor}"
                for stat in ("current", "mean5", "slope5")
                for sensor in ("temperature", "pressure", "vibration")
            ],
            "synthetic-normal-residual-v1",
        ),
    ):
        tasks[task] = dict(
            track=track,
            unit=unit,
            horizon=horizon,
            names=feature_names,
            version=version,
            feature_version=version,
            schema_hash=schema_hash(feature_names, track, unit, version),
        )
    files = {
        p.relative_to(output).as_posix(): file_hash(p)
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    manifest = dict(
        version="fleetiq-bundle-v1",
        files=files,
        tasks=tasks,
        failure=dict(
            weights=weights,
            threshold=candidate["tune"]["threshold"],
            selection_hash=selection["selection_hash"],
        ),
        rul_intervals=intervals["calibration"],
        evidence={
            name: file_hash(ROOT / "docs/exports" / name)
            for name in (
                "failure_improvement_final.json",
                "rul_evaluation.json",
                "anomaly_robustness.json",
            )
        },
        dependencies=file_hash(ROOT / "uv.lock"),
        source_hashes={
            p: file_hash(ROOT / p)
            for p in (
                "ml/training/fleetiq_training/degradation_features.py",
                "packages/features/fleetiq_features/pipeline.py",
            )
        },
        training_scope="NASA FD001/FD003; anomaly controlled synthetic fixture only",
        uploaded_files_allowed=False,
    )
    write_json(output / "manifest.json", manifest)
    digest = file_hash(output / "manifest.json")
    Bundle(output, digest)
    write_json(
        ROOT / "docs/exports/bundle_build.json",
        dict(
            path=output.relative_to(ROOT).as_posix(),
            approved_digest=digest,
            evidence=manifest["evidence"],
        ),
    )
    return digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_bundle(experiment(args.config), args.output))


if __name__ == "__main__":
    main()
