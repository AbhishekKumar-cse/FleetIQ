"""Global calendar phases, purged horizons and held-out installation cohorts.

Final-future samples are inventory only: their labels are never consumed here.
NASA native cycles are deliberately unsupported by this protocol.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from fleetiq_data.eda import training_group
from fleetiq_data.quality.time import utc

from fleetiq_evaluation.splits import content_hash, load_config


@dataclass(frozen=True)
class CalendarSample:
    sample_id: str
    installation_id: str
    aircraft_id: str
    as_of: datetime
    window_start: datetime
    recorded_at: datetime
    label_end: datetime
    label_recorded_at: datetime | None
    eligible: bool
    label: bool | None
    event_id: str | None = None
    history_count: int = 0
    reason: str = "unknown"


def _validate(sample, horizon, lookback, *, validate_label=True):
    times = [sample.as_of, sample.window_start, sample.recorded_at, sample.label_end]
    if validate_label and sample.label_recorded_at is not None:
        times.append(sample.label_recorded_at)
    for value in times:
        if not isinstance(value, datetime):
            raise ValueError("Calendar protocol excludes NASA native cycles")
        utc(value)
    if sample.window_start != sample.as_of - lookback or sample.label_end != sample.as_of + horizon:
        raise ValueError("Declared lookback/horizon mismatch")
    if (
        validate_label
        and sample.eligible
        and (sample.label is None or sample.label_recorded_at is None)
    ):
        raise ValueError("Eligible label requires recorded availability")
    if validate_label and sample.label is not None and type(sample.label) is not bool:
        raise ValueError("Boolean labels required")
    if validate_label and not sample.eligible and sample.label is not None:
        raise ValueError("Censored/intervened labels must remain unknown")


def calendar_partitions(samples, config=None, *, track="synthetic_engine_demo"):
    if track != "synthetic_engine_demo":
        raise ValueError("Calendar protocol cannot assign dates to NASA cycles")
    cfg = (config or load_config())["calendar"]
    boundaries = [
        utc(cfg[key]) for key in ("start", "fit_end", "tune_end", "calibration_end", "future_end")
    ]
    if boundaries != sorted(set(boundaries)):
        raise ValueError("Strictly ordered global calendar boundaries required")
    horizon = timedelta(hours=cfg["horizon_hours"])
    lookback = timedelta(seconds=cfg["lookback_seconds"])
    embargo = timedelta(seconds=cfg["embargo_seconds"])
    if horizon <= timedelta(0) or lookback <= timedelta(0) or embargo < lookback:
        raise ValueError("Positive horizon/lookback and at least one-lookback embargo required")
    samples = list(samples)
    if any(not isinstance(row.as_of, datetime) for row in samples):
        raise ValueError("Calendar protocol excludes NASA native cycles")
    samples = sorted(samples, key=lambda row: (row.as_of, row.installation_id, row.sample_id))
    if len({row.sample_id for row in samples}) != len(samples):
        raise ValueError("Duplicate temporal sample identity")
    for row in samples:
        _validate(row, horizon, lookback, validate_label=row.as_of < boundaries[3])
    heldout = {row.installation_id for row in samples if not training_group(row.aircraft_id)}
    groups = {
        name: []
        for name in (
            "fit",
            "tune",
            "calibration",
            "known_asset_future",
            "new_asset_future",
            "held_out_installation",
        )
    }
    excluded = {}
    seen_fit = set()
    for row in samples:
        role = None
        reason = "outside_calendar"
        for index, name in enumerate(("fit", "tune", "calibration")):
            start, end = boundaries[index : index + 2]
            if start <= row.as_of < end:
                if row.installation_id in heldout:
                    reason = "held_out_installation_before_future"
                elif row.recorded_at > row.as_of:
                    reason = "feature_unavailable_at_as_of"
                elif row.as_of < start + embargo or row.window_start < start:
                    reason = "lookback_embargo"
                elif row.label_end > end:
                    reason = "horizon_crosses_phase"
                elif not row.eligible:
                    reason = row.reason
                elif row.label_recorded_at > end:
                    reason = "label_unavailable_at_cutoff"
                else:
                    role = name
                    if name == "fit":
                        seen_fit.add(row.installation_id)
                break
        if boundaries[3] <= row.as_of < boundaries[4]:
            # Feature availability and embargo still apply, but final labels are opaque.
            if row.recorded_at > row.as_of:
                reason = "feature_unavailable_at_as_of"
            elif row.as_of < boundaries[3] + embargo or row.window_start < boundaries[3]:
                reason = "lookback_embargo"
            elif row.label_end > boundaries[4]:
                reason = "horizon_crosses_phase"
            elif row.installation_id in heldout:
                role = "held_out_installation"
            elif row.installation_id in seen_fit:
                role = "known_asset_future"
            else:
                role = "new_asset_future"
        if role:
            groups[role].append(row.sample_id)
        else:
            excluded[row.sample_id] = reason
    manifest = dict(
        version=cfg["version"],
        track=track,
        groups=groups,
        excluded=excluded,
        counts={name: len(ids) for name, ids in groups.items()},
        boundaries=[value.isoformat() for value in boundaries],
        horizon_hours=cfg["horizon_hours"],
        lookback_seconds=cfg["lookback_seconds"],
        embargo_seconds=cfg["embargo_seconds"],
        held_out_installations=sorted(heldout),
        final_future_labels_accessed=False,
        input_hash=content_hash(
            [
                dict(
                    id=row.sample_id,
                    installation=row.installation_id,
                    aircraft=row.aircraft_id,
                    as_of=row.as_of,
                    recorded_at=row.recorded_at,
                )
                for row in samples
            ]
        ),
    )
    # Only development-label availability is part of this manifest's provenance.
    manifest["development_labels_hash"] = content_hash(
        [
            dict(
                id=row.sample_id,
                label=row.label,
                eligible=row.eligible,
                available=row.label_recorded_at,
                reason=row.reason,
                event_id=row.event_id,
            )
            for row in samples
            if row.as_of < boundaries[3]
        ]
    )
    return manifest | {"manifest_hash": content_hash(manifest)}


def expanding_folds(samples, config=None):
    cfg = config or load_config()
    result = []
    for fold in cfg["calendar"]["expanding_folds"]:
        calendar = cfg["calendar"] | fold
        result.append(calendar_partitions(samples, cfg | {"calendar": calendar}))
    return result
