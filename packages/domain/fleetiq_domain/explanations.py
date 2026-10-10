"""Signed associations in their verified model scale, never causal risk shares."""

import numpy as np
import shap
from fleetiq_evaluation.splits import content_hash


def tree_explanation(model, values, names, background, *, scale, model_hash, feature_version):
    x, background = np.asarray(values, dtype=float), np.asarray(background, dtype=float)
    if (
        x.ndim != 2
        or background.ndim != 2
        or x.shape[1] != len(names)
        or background.shape[1] != len(names)
        or not len(background)
        or len(background) > 128
        or len(names) != len(set(names))
        or not np.isfinite(x).all()
        or not np.isfinite(background).all()
        or scale not in {"cycles", "raw_log_odds"}
    ):
        raise ValueError("Finite ordered features and fixed bounded training background required")
    explainer = shap.TreeExplainer(
        model, data=background, model_output="raw", feature_perturbation="interventional"
    )
    effects = explainer(x, check_additivity=True)
    if scale == "raw_log_odds":
        import xgboost as xgb

        raw = model.get_booster().predict(xgb.DMatrix(x), output_margin=True)
    else:
        raw = model.predict(x)
    base = np.asarray(effects.base_values).reshape(-1)
    contributions = np.asarray(effects.values)
    reconstructed = base + contributions.sum(axis=1)
    if not np.allclose(reconstructed, raw, atol=2e-5, rtol=2e-5):
        raise ValueError("Explanation does not reconstruct declared model output")
    return dict(
        supported=True,
        scale=scale,
        base_values=base.tolist(),
        effects=contributions.tolist(),
        feature_names=list(names),
        raw_outputs=np.asarray(raw).tolist(),
        model_hash=model_hash,
        feature_version=feature_version,
        background_hash=content_hash(background.tolist()),
        background_role="fit_only",
        causal=False,
        percentages=None,
        correlated_features_warning=True,
    )


def component_ensemble_explanation(components, weights, actual_score):
    if (
        len(components) != len(weights)
        or not np.isclose(sum(weights), 1)
        or any(w < 0 for w in weights)
    ):
        raise ValueError("Frozen ensemble components and weights required")
    margins = []
    for component in components:
        if not component["supported"] or component["scale"] != "raw_log_odds":
            raise ValueError("Verified raw-log-odds component explanation required")
        effects = np.array(component["effects"])
        raw = np.array(component["raw_outputs"])
        if not np.allclose(
            np.array(component["base_values"]) + effects.sum(1), raw, atol=2e-5, rtol=2e-5
        ):
            raise ValueError("Component additive consistency failed")
        margins.append(raw)
    scores = np.column_stack([1 / (1 + np.exp(-np.clip(m, -709, 709))) for m in margins]) @ weights
    if not np.allclose(scores, actual_score, atol=2e-6):
        raise ValueError("Component margins do not match actual weighted score")
    return dict(
        supported=False,
        scope="component_only",
        components=components,
        weights=list(weights),
        actual_score=np.asarray(actual_score).tolist(),
        aggregate_effects=None,
        reason="raw_log_odds_effects_cannot_be_averaged_to_explain_mean_probability",
        causal=False,
    )


def unusual_channels(residual_vector):
    values = np.asarray(residual_vector, dtype=float)
    if values.shape != (9,) or not np.isfinite(values).all():
        raise ValueError("Nine supported context residual features required")
    channels = ("temperature_c", "pressure_kpa", "vibration_mm_s")
    return dict(
        kind="observed_baseline_departure",
        attribution=False,
        diagnosis=None,
        channels=[
            dict(channel=channels[i], standardized_departure=float(values[i]))
            for i in np.argsort(-np.abs(values[:3]))
        ],
    )
