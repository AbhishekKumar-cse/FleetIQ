"""One causal snapshot path for offline extraction and online serving.

Artifacts are inspectable JSON rather than executable serialized Python objects.
Raw nulls, observation masks and coverage survive numeric model imputation.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
from fleetiq_data.eda import training_group
from sklearn.impute import SimpleImputer

from fleetiq_features.basic import basic_features
from fleetiq_features.context import ContextFit, context_features, fit_context
from fleetiq_features.domain import domain_features, visible_records
from fleetiq_features.schema import CONFIG as BASIC_CONFIG
from fleetiq_features.schema import ROOT, clock, eligible, select_window, track_schema
from fleetiq_features.temporal import CONFIG as TEMPORAL
from fleetiq_features.temporal import TrainingBaseline, fit_baseline, temporal_features

CONFIG = json.loads((ROOT / "config/features_v3.json").read_text())


def digest(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, allow_nan=False, default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class SnapshotInput:
    track: str
    stream: str
    group: str
    as_of: datetime | int
    channels: dict
    units: tuple[str, ...]
    settings: dict = field(default_factory=dict)
    technical: tuple = ()
    context: tuple = ()


def expected_units(track):
    schema = track_schema(track)
    units = schema["units"]
    return tuple(units if isinstance(units, list) else [units] * len(schema["channels"]))


def causal_input(spec):
    schema = track_schema(spec.track)
    end = clock(spec.as_of, spec.track)
    if spec.units != expected_units(spec.track) or tuple(spec.channels) != tuple(
        schema["channels"]
    ):
        raise ValueError("Track unit/channel layout mismatch")
    window = max(TEMPORAL["tracks"][spec.track]["windows"])
    start = end - window if spec.track == "cmapss_benchmark" else end - timedelta(seconds=window)
    channels = {
        name: select_window(rows, track=spec.track, as_of=end, window_start=start)
        for name, rows in spec.channels.items()
    }
    if any(row.stream != spec.stream for rows in channels.values() for row in rows):
        raise ValueError("Snapshot stream mismatch")
    settings = {}
    technical, context = [], []
    if spec.track == "cmapss_benchmark":
        if (
            spec.technical
            or spec.context
            or set(spec.settings) - {"setting_1", "setting_2", "setting_3"}
        ):
            raise ValueError("NASA native cycles cannot accept calendar/installation context")
        settings = {
            name: select_window(
                spec.settings.get(name, ()), track=spec.track, as_of=end, window_start=start
            )
            for name in ("setting_1", "setting_2", "setting_3")
        }
        if any(row.stream != spec.stream for rows in settings.values() for row in rows):
            raise ValueError("Setting stream mismatch")
    else:
        if spec.settings:
            raise ValueError("NASA settings cannot enter synthetic features")
        technical = visible_records(spec.technical, installation_id=spec.stream, as_of=end)
        context = [
            row
            for row in spec.context
            if row.stream == spec.stream
            and clock(row.occurred_at, spec.track) <= end
            and clock(row.recorded_at, spec.track) <= end
        ]
    body = dict(
        track=spec.track,
        stream=spec.stream,
        as_of=end,
        units=spec.units,
        channels={
            key: [
                [row.at, row.value, row.recorded_at, row.quality, row.imputed, row.stream]
                for row in rows
            ]
            for key, rows in channels.items()
        },
        settings={
            key: [
                [row.at, row.value, row.recorded_at, row.quality, row.imputed, row.stream]
                for row in rows
            ]
            for key, rows in settings.items()
        },
        technical=[asdict(row) for row in technical],
        context=[asdict(row) for row in context],
    )
    return end, start, channels, settings, technical, context, digest(body)


def raw_snapshot(spec, *, baselines=None, context_fit=None):
    end, start, channels, settings, technical, context, input_hash = causal_input(spec)
    basic = basic_features(channels, track=spec.track, as_of=end, window_start=start)
    temporal = temporal_features(channels, track=spec.track, as_of=end, baselines=baselines)
    features = basic["features"] | temporal["features"]
    if spec.track == "cmapss_benchmark":
        for name, rows in settings.items():
            value = float(rows[-1].value) if rows and eligible(rows[-1]) else None
            features[f"context.{name}"] = value
            features[f"context.{name}_missing"] = int(value is None)
    else:
        features.update(
            {
                f"domain.{name}": value
                for name, value in domain_features(
                    technical, installation_id=spec.stream, as_of=end
                ).items()
            }
        )
        if context_fit is not None:
            if context_fit.track != spec.track:
                raise ValueError("Context track mismatch")
            latest = {
                name: float(rows[-1].value) if rows and eligible(rows[-1]) else None
                for name, rows in channels.items()
            }
            features.update(
                context_features(
                    context, stream=spec.stream, as_of=end, fit=context_fit, sensors=latest
                )
            )
    counts = {name: sum(eligible(row) for row in rows) for name, rows in channels.items()}
    # Declared source cadence: NASA one row/cycle; synthetic demo one row/hour.
    expected = max(TEMPORAL["tracks"][spec.track]["windows"])
    if spec.track == "synthetic_engine_demo":
        expected = max(1, expected // 3600)
    history = max((len(rows) for rows in channels.values()), default=0)
    features["quality.history_count"] = history
    features["quality.short_history"] = int(history < expected)
    coverage = {name: min(1.0, count / expected) for name, count in counts.items()}
    supported = all(
        count > 0 and coverage[name] >= CONFIG["minimum_coverage"] for name, count in counts.items()
    )
    return dict(
        features=features,
        input_hash=input_hash,
        observed_coverage=coverage,
        masks=dict(basic=basic["masks"], temporal=temporal["masks"]),
        supported=supported,
        short_history=history < expected,
        strategy=CONFIG["short_history_strategy"],
    )


@dataclass(frozen=True)
class PipelineFit:
    track: str
    names: tuple[str, ...]
    medians: tuple[float, ...]
    all_missing: tuple[str, ...]
    baselines: tuple[TrainingBaseline, ...]
    context_fit: ContextFit | None
    training_groups: tuple[str, ...]
    training_input_hash: str
    schema_hash: str
    content_hash: str

    def body(self):
        return {key: value for key, value in asdict(self).items() if key != "content_hash"}

    @lru_cache(maxsize=32)
    def validate(self):
        schema = schema_digest(self.track, self.names)
        if schema != self.schema_hash or digest(self.body()) != self.content_hash:
            raise ValueError("Feature artifact integrity/schema mismatch")
        if len(self.names) != len(self.medians) or not all(math.isfinite(v) for v in self.medians):
            raise ValueError("Invalid finite imputer layout")
        if not self.training_groups or any(
            not training_group(group) for group in self.training_groups
        ):
            raise ValueError("Artifact crossed the EDA training boundary")

    def save(self, path):
        self.validate()
        path.write_text(json.dumps(asdict(self), sort_keys=True, indent=2, allow_nan=False))

    @classmethod
    def load(cls, path):
        body = json.loads(path.read_text())
        for key in ("names", "medians", "all_missing", "training_groups"):
            body[key] = tuple(body[key])
        body["baselines"] = tuple(
            TrainingBaseline(**(row | {"training_groups": tuple(row["training_groups"])}))
            for row in body["baselines"]
        )
        if body["context_fit"]:
            row = body["context_fit"]
            body["context_fit"] = ContextFit(
                row["track"],
                tuple(row["groups"]),
                tuple(row["centers"]),
                tuple(row["categories"]),
                tuple(tuple(v) for v in row["baselines"]),
                row["content_hash"],
            )
        fit = cls(**body)
        fit.validate()
        return fit


def schema_digest(track, names):
    return digest(
        dict(
            track=track,
            names=names,
            units=expected_units(track),
            basic=BASIC_CONFIG,
            temporal=TEMPORAL,
            pipeline=CONFIG,
        )
    )


def fit_pipeline(specs, *, track, partition="train", context_fit=None):
    specs = list(specs)
    groups = tuple(sorted({str(spec.group) for spec in specs}))
    if (
        partition != "train"
        or not specs
        or any(spec.track != track or not training_group(spec.group) for spec in specs)
    ):
        raise ValueError("Only EDA-approved training groups may fit preprocessing")
    if track == "cmapss_benchmark" and any(
        spec.stream != f"NASA:FD001:train:{spec.group}" for spec in specs
    ):
        raise ValueError("Official test/unqualified benchmark identities cannot fit preprocessing")
    if context_fit is not None and (
        context_fit.track != track or set(context_fit.groups) - set(groups)
    ):
        raise ValueError("Context fit must share the training boundary")
    if track == "synthetic_engine_demo" and context_fit is None:
        context_rows = []
        for spec in specs:
            _, _, channels, _, _, records, _ = causal_input(spec)
            records.sort(key=lambda row: (row.occurred_at, row.recorded_at))
            latest = records[-1] if records else None
            context_rows.append(
                dict(
                    group=spec.group,
                    workload=latest.workload if latest else None,
                    ambient_temperature_c=latest.ambient_temperature_c if latest else None,
                    regime=latest.regime if latest else None,
                    sensors={
                        name: float(rows[-1].value)
                        for name, rows in channels.items()
                        if rows and eligible(rows[-1])
                    },
                )
            )
        context_fit = fit_context(
            context_rows, track=track, partition="train", training_groups=groups
        )
    values = {name: [] for name in track_schema(track)["channels"]}
    for spec in specs:
        _, _, channels, *_ = causal_input(spec)
        for name, rows in channels.items():
            if rows and eligible(rows[-1]) and rows[-1].at == spec.as_of:
                values[name].append(float(rows[-1].value))
    baselines = tuple(
        fit_baseline(
            v,
            channel=name,
            track=track,
            partition="train",
            training_groups=groups,
            version=CONFIG["version"],
        )
        for name, v in values.items()
        if v
    )
    frozen = {baseline.channel: baseline for baseline in baselines}
    names = tuple(raw_snapshot(specs[0], baselines=frozen, context_fit=context_fit)["features"])
    matrix = np.empty((len(specs), len(names)), dtype=float)
    input_hashes = []
    for index, spec in enumerate(specs):
        row = raw_snapshot(spec, baselines=frozen, context_fit=context_fit)
        if tuple(row["features"]) != names:
            raise ValueError("Inconsistent feature layout")
        matrix[index] = [row["features"][name] for name in names]
        input_hashes.append(row["input_hash"])
    imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(matrix)
    medians = tuple(float(value) for value in imputer.statistics_)
    all_missing = tuple(name for i, name in enumerate(names) if np.isnan(matrix[:, i]).all())
    body = dict(
        track=track,
        names=names,
        medians=medians,
        all_missing=all_missing,
        baselines=baselines,
        context_fit=context_fit,
        training_groups=groups,
        training_input_hash=digest(input_hashes),
        schema_hash=schema_digest(track, names),
    )
    provisional = PipelineFit(**body, content_hash="")
    fit = PipelineFit(**body, content_hash=digest(provisional.body()))
    fit.validate()
    return fit


def snapshot(spec, fit):
    fit.validate()
    if spec.track != fit.track:
        raise ValueError("Frozen artifact track mismatch")
    raw = raw_snapshot(
        spec, baselines={row.channel: row for row in fit.baselines}, context_fit=fit.context_fit
    )
    if tuple(raw["features"]) != fit.names:
        raise ValueError("Frozen feature layout mismatch")
    missing = tuple(value is None for value in raw["features"].values())
    vector = tuple(
        median if value is None else float(value)
        for value, median in zip(raw["features"].values(), fit.medians, strict=True)
    )
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Nonfinite model features")
    return raw | dict(
        vector=vector,
        imputed_features=missing,
        names=fit.names,
        schema_hash=fit.schema_hash,
        fit_hash=fit.content_hash,
        unsupported_all_missing=fit.all_missing,
    )
