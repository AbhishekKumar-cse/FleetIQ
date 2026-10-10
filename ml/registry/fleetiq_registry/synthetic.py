"""Separate native-hour inference contract, never a NASA adapter."""

import json
from pathlib import Path

import numpy as np
from fleetiq_training.anomaly import forest_score
from fleetiq_training.calibration import probabilities
from fleetiq_training.classification import predict_controlled
from fleetiq_training.synthetic_track import NAMES, features
from xgboost import XGBClassifier, XGBRegressor

from fleetiq_registry.bundle import verify_files


class SyntheticHourBundle:
    def __init__(self, folder, approved_hash):
        self.folder = Path(folder).resolve()
        self.manifest = json.loads((self.folder / "manifest.json").read_text())
        verify_files(self.folder, self.manifest, approved_hash)
        if (
            self.manifest["version"] != "fleetiq-synthetic-hours-v1"
            or self.manifest["feature_names"] != NAMES
        ):
            raise ValueError("Synthetic bundle contract mismatch")
        self.hash = approved_hash
        self.pre = json.loads((self.folder / "preprocessing.json").read_text())
        self.selection = json.loads((self.folder / "selection.json").read_text())
        self.logistic = json.loads((self.folder / "logistic.json").read_text())
        self.classifier = XGBClassifier(n_jobs=2)
        self.classifier.load_model(self.folder / "classifier.json")
        self.ridge = json.loads((self.folder / "ridge.json").read_text())
        self.regressor = None
        if self.selection["rul_kind"] == "xgboost":
            self.regressor = XGBRegressor(n_jobs=2)
            self.regressor.load_model(self.folder / "regressor.json")
        self.anomaly = json.loads((self.folder / "anomaly.json").read_text())
        self.calibration = json.loads((self.folder / "calibration.json").read_text())
        self.intervals = json.loads((self.folder / "intervals.json").read_text())

    def predict(
        self,
        observed,
        *,
        track="synthetic_sensor_model",
        schema_version="synthetic-engine-v1",
        unit="operating_hours",
        horizon_hours=24,
    ):
        if (track, schema_version, unit, horizon_hours) != (
            "synthetic_sensor_model",
            "synthetic-engine-v1",
            "operating_hours",
            24,
        ):
            raise ValueError(
                "Synthetic-hour bundle cannot accept NASA/native-cycle or other horizons"
            )
        rows = features(observed, self.pre["context"])
        last = rows[-1]
        identity = dict(
            track=track,
            unit=unit,
            horizon_hours=horizon_hours,
            bundle_hash=self.hash,
            feature_version=self.pre["feature_version"],
            scope=self.manifest["applicability"],
            changes_serviceability=False,
        )
        if last["vector"] is None:
            return identity | dict(coverage="unsupported", output=None, reason=last["quality"])
        x = np.array([last["vector"]])
        weight = self.selection["xgboost_weight"]
        raw = float(
            (
                weight * self.classifier.predict_proba(x)[:, 1]
                + (1 - weight) * predict_controlled(self.logistic, x)
            )[0]
        )
        if self.regressor:
            remaining = float(self.regressor.predict(x)[0])
        else:
            remaining = float(
                (
                    ((x - np.array(self.ridge["center"])) / np.array(self.ridge["scale"]))
                    @ np.array(self.ridge["coefficients"])
                    + self.ridge["intercept"]
                )[0]
            )
        anomaly_scores = forest_score(self.anomaly["forest"], x[:, :9])
        persistent = (
            len(rows) >= 2
            and rows[-2]["vector"] is not None
            and float(forest_score(self.anomaly["forest"], [rows[-2]["vector"][:9]])[0])
            > self.anomaly["threshold"]
            and float(anomaly_scores[0]) > self.anomaly["threshold"]
        )
        calibrated = probabilities([raw], self.calibration)
        display = self.manifest["probability_display_enabled"] and calibrated["supported"]
        return identity | dict(
            coverage="full",
            output=dict(
                raw_risk_score=raw,
                review=raw >= self.selection["risk"]["threshold"],
                calibrated_probability=float(calibrated["probability"][0]) if display else None,
                raw_rul_hours=remaining,
                remaining_operating_hours=max(0, remaining),
                rul_interval=None,
                interval_display_enabled=False,
                anomaly_score=float(anomaly_scores[0]),
                persistent_anomaly=persistent,
                failure_probability_from_anomaly=None,
            ),
        )
