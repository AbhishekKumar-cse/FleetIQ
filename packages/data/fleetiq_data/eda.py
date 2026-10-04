"""Fixed training partitions and allowlisted observed inputs; no test/evaluator access."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from fleetiq_data.contracts import NASA_COLUMNS
from fleetiq_data.importer import UNIT_FIELDS
from fleetiq_data.quality.profiles import load_profiles
from fleetiq_data.quality.time import classify_time, utc
from fleetiq_data.quality.values import validate_value
from fleetiq_data.targets import synthetic_hour_target, training_cycle_targets

ROOT = Path(__file__).resolve().parents[3]
PARTITION = json.loads((ROOT / "config/eda_partition.json").read_text())
SYNTHETIC_COLUMNS = [
    "aircraft_id",
    "component_serial",
    "installation_id",
    "measured_at",
    "recorded_at",
    "operating_hours",
    "component_age_hours",
    "workload",
    "ambient_temperature_c",
    "temperature_c",
    "oil_pressure_kpa",
    "vibration_mm_s",
    *UNIT_FIELDS.values(),
]
ALLOWED = {
    "processed/cmapss/train.parquet",
    "synthetic/observed/sensor_observations.parquet",
    "synthetic/observed/confirmed_faults.parquet",
}


def training_group(identifier):
    return int(hashlib.sha256(str(identifier).encode()).hexdigest()[:8], 16) % 100 < 60


def _read(root, relative, **kwargs):
    if relative not in ALLOWED:
        raise ValueError("EDA access is restricted to training/observed allowlist")
    path = Path(root).resolve() / relative
    if path.resolve() != path:
        raise ValueError("EDA inputs cannot redirect through symbolic links")
    return pd.read_parquet(path, **kwargs)


def load_training(root, track):
    if track == "cmapss_benchmark":
        path, group, columns = "processed/cmapss/train.parquet", "unit_id", list(NASA_COLUMNS)
    elif track == "synthetic_engine_demo":
        path, group, columns = (
            "synthetic/observed/sensor_observations.parquet",
            "aircraft_id",
            SYNTHETIC_COLUMNS,
        )
    else:
        raise ValueError("Training track required")
    identities = _read(root, path, columns=[group])[group].unique()
    groups = [identifier for identifier in identities if training_group(identifier)]
    frame = _read(root, path, columns=columns, filters=[(group, "in", groups)])
    if track == "synthetic_engine_demo":
        cutoff = utc(PARTITION["label_cutoff"])
        frame = frame.loc[
            (pd.to_datetime(frame.measured_at, utc=True) <= cutoff)
            & (pd.to_datetime(frame.recorded_at, utc=True) <= cutoff)
        ].copy()
        profiles = load_profiles("synthetic_engine")
        frame["chronology_eligible"] = [
            classify_time(at, recorded, now=cutoff, mode="historical") == "eligible"
            for at, recorded in zip(frame.measured_at, frame.recorded_at, strict=True)
        ]
        for channel, profile in profiles.items():
            values = [
                validate_value(
                    row[channel],
                    row[UNIT_FIELDS[channel]],
                    profile,
                    pressure_kind="absolute" if profile.pressure_kind else None,
                )
                for row in frame.to_dict("records")
            ]
            frame[channel + "_raw_missing"] = frame[channel].isna()
            frame[channel] = [value.value for value in values]
            frame[channel + "_eligible"] = [value.value is not None for value in values]
            frame.loc[~frame.chronology_eligible, channel] = float("nan")
        frame["workload_band"] = pd.cut(
            frame.workload,
            [-float("inf"), 0.8, 1.1, float("inf")],
            labels=["low", "medium", "high"],
        )
    frame.attrs.update(partition=PARTITION["version"], track=track)
    return frame


def training_labels(root, frame, track):
    if frame.attrs.get("partition") != PARTITION["version"] or frame.attrs.get("track") != track:
        raise ValueError("Training provenance required")
    if track == "cmapss_benchmark":
        if not all(training_group(i) for i in frame.unit_id.unique()):
            raise ValueError("Training groups required")
        return training_cycle_targets(frame, 30)
    if track != "synthetic_engine_demo" or not all(
        training_group(i) for i in frame.aircraft_id.unique()
    ):
        raise ValueError("Training-only feature frame required")
    frame = frame.loc[frame.chronology_eligible].copy()
    faults = _read(
        root,
        "synthetic/observed/confirmed_faults.parquet",
        filters=[("component_serial", "in", list(frame.component_serial.unique()))],
    )
    cutoff = utc(PARTITION["label_cutoff"])
    known = faults.loc[pd.to_datetime(faults.recorded_at, utc=True) <= cutoff].copy()
    results = []
    for serial, observations in frame.groupby("component_serial"):
        matches = known.loc[known.component_serial == serial]
        evidence = matches.iloc[0] if len(matches) else None
        for _, row in observations.iterrows():
            result = synthetic_hour_target(
                as_of=utc(row.measured_at),
                label_cutoff=cutoff,
                horizon_hours=PARTITION["risk_horizon_hours"],
                event_time=utc(evidence.event_time)
                if evidence is not None and evidence.status == "confirmed"
                else None,
                event_recorded_at=utc(evidence.recorded_at)
                if evidence is not None and evidence.status == "confirmed"
                else None,
                followup_until=utc(evidence.followup_until) if evidence is not None else None,
                followup_recorded_at=utc(evidence.followup_recorded_at)
                if evidence is not None
                else None,
            )
            results.append(
                {
                    "component_serial": serial,
                    "as_of": row.measured_at,
                    "eligible": result.eligible,
                    "failure_within_horizon": result.failure_within_horizon,
                    "reason": result.reason,
                }
            )
    events = known.loc[
        (known.status == "confirmed") & (pd.to_datetime(known.event_time, utc=True) <= cutoff)
    ]
    return pd.DataFrame(results), events


def render_report(root, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    synthetic = load_training(root, "synthetic_engine_demo")
    nasa = load_training(root, "cmapss_benchmark")
    labels, events = training_labels(root, synthetic, "synthetic_engine_demo")
    nasa_labels = training_labels(root, nasa, "cmapss_benchmark")
    channels = list(UNIT_FIELDS)
    synthetic[channels].hist(bins=30, figsize=(12, 4))
    plt.suptitle("Synthetic training: canonical sensor distributions")
    plt.tight_layout()
    plt.savefig(output / "distributions.png")
    plt.close("all")
    first = synthetic.loc[synthetic.component_serial == synthetic.component_serial.iloc[0]].copy()
    first["measured_at"] = pd.to_datetime(first.measured_at, utc=True)
    first.plot(x="measured_at", y=channels, subplots=True, figsize=(10, 6))
    plt.tight_layout()
    plt.savefig(output / "trends.png")
    plt.close("all")
    fig, ax = plt.subplots()
    im = ax.imshow(
        synthetic[channels + ["workload", "ambient_temperature_c"]].corr(),
        vmin=-1,
        vmax=1,
        cmap="coolwarm",
    )
    ax.set_xticks(range(5), channels + ["workload", "ambient"], rotation=45, ha="right")
    ax.set_yticks(range(5), channels + ["workload", "ambient"])
    fig.colorbar(im)
    fig.tight_layout()
    fig.savefig(output / "correlations.png")
    plt.close(fig)
    synthetic[[channel + "_raw_missing" for channel in channels]].mean().plot.bar(
        title="Training raw missing fractions"
    )
    plt.tight_layout()
    plt.savefig(output / "missingness.png")
    plt.close("all")
    events.groupby("component_serial").size().plot.bar(
        title="Independent confirmed training failures by component"
    )
    plt.tight_layout()
    plt.savefig(output / "failures.png")
    plt.close("all")
    labels.loc[labels.eligible, "failure_within_horizon"].value_counts().plot.bar(
        title="Mature 24-hour training labels"
    )
    plt.tight_layout()
    plt.savefig(output / "imbalance.png")
    plt.close("all")
    synthetic.assign(age_band=(synthetic.component_age_hours // 24) * 24).groupby(
        ["workload_band", "age_band"], observed=True
    ).vibration_mm_s.mean().unstack(0).plot(
        title="Observed workload bands: training age/vibration association"
    )
    plt.tight_layout()
    plt.savefig(output / "regimes.png")
    plt.close("all")
    nasa.plot(
        x="cycle",
        y="sensor_4",
        kind="scatter",
        s=1,
        title="NASA training subset: sensor 4 versus native cycle",
    )
    plt.tight_layout()
    plt.savefig(output / "nasa_cycles.png")
    plt.close("all")
    findings = {
        "partition": PARTITION,
        "synthetic_rows": len(synthetic),
        "training_aircraft": synthetic.aircraft_id.nunique(),
        "training_components": synthetic.component_serial.nunique(),
        "independent_confirmed_events": events.event_id.nunique(),
        "independent_event_aircraft": synthetic.loc[
            synthetic.component_serial.isin(events.component_serial), "aircraft_id"
        ].nunique(),
        "mature_label_counts": {
            str(k): int(v)
            for k, v in labels.loc[labels.eligible, "failure_within_horizon"].value_counts().items()
        },
        "ineligible_label_rows": int((~labels.eligible).sum()),
        "nasa_training_rows": len(nasa),
        "nasa_training_engines": nasa.unit_id.nunique(),
        "nasa_30_cycle_label_counts": {
            str(k): int(v)
            for k, v in nasa_labels.loc[nasa_labels.eligible, "failure_within_horizon"]
            .value_counts()
            .items()
        },
        "risk_calibration": "unsupported until independent held-out events and label maturity are validated",
        "synthetic_rul": "unsupported: censored/replaced operational histories do not establish complete terminal life",
        "regimes": "observed workload bands only; latent generator regime/truth is inaccessible",
    }
    (output / "findings.json").write_text(json.dumps(findings, indent=2))
    return findings
