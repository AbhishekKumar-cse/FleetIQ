"""Group-held-out model tournament without access to either official test set."""

import argparse
import json
import time
import warnings
from itertools import product

import numpy as np
import yaml
from catboost import CatBoostClassifier
from fleetiq_evaluation.splits import ROOT, content_hash
from lightgbm import LGBMClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_selection import VarianceThreshold
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from xgboost import XGBClassifier

from fleetiq_training.classification import file_hash, sigmoid, write_json
from fleetiq_training.degradation_features import build_table, development, endpoint_indices


def scores_metrics(y, scores, threshold):
    y, scores = np.asarray(y, dtype=int), np.asarray(scores, dtype=float)
    if (
        len(y) != len(scores)
        or not len(y)
        or not np.isfinite(scores).all()
        or np.any((scores < 0) | (scores > 1))
        or set(np.unique(y)) - {0, 1}
    ):
        raise ValueError("Finite bounded scores and binary matching outcomes required")
    alert = scores >= threshold
    tp = int(np.sum(alert & (y == 1)))
    fp = int(np.sum(alert & (y == 0)))
    fn = int(np.sum(~alert & (y == 1)))
    tn = int(np.sum(~alert & (y == 0)))
    return dict(
        samples=len(y),
        positives=int(y.sum()),
        tp=tp,
        tn=tn,
        fp=fp,
        fn=fn,
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        f2=5 * tp / (5 * tp + 4 * fn + fp) if 5 * tp + 4 * fn + fp else None,
        accuracy=(tp + tn) / len(y),
        average_precision=float(average_precision_score(y, scores))
        if len(np.unique(y)) == 2
        else None,
        roc_auc=float(roc_auc_score(y, scores)) if len(np.unique(y)) == 2 else None,
        raw_score_brier=float(brier_score_loss(y, scores)),
        threshold=float(threshold),
    )


def choose_threshold(y, scores, minimum_precision):
    if set(np.unique(y)) != {0, 1} or not 0 < minimum_precision <= 1:
        raise ValueError("Two-class development outcomes and valid precision floor required")
    options = []
    for threshold in np.unique(np.r_[0, scores]):
        result = scores_metrics(y, scores, threshold)
        if result["precision"] is not None and result["precision"] >= minimum_precision:
            # Window/endpoint sensitivity, not one-hit-per-engine event recall.
            options.append(((result["f2"], result["recall"], result["precision"]), result))
    if not options:
        raise ValueError("No development threshold meets precision floor")
    return max(options, key=lambda item: item[0])[1]


def estimator(family, settings, cfg):
    common = dict(random_state=cfg["seed"])
    if family in {"baseline_xgboost", "xgboost"}:
        return XGBClassifier(
            n_estimators=settings["iterations"],
            max_depth=settings["depth"],
            learning_rate=0.05,
            min_child_weight=5,
            reg_lambda=5,
            reg_alpha=0.1,
            subsample=0.85,
            colsample_bytree=0.85,
            tree_method="hist",
            device="cpu",
            n_jobs=cfg["threads"],
            **common,
        )
    if family == "catboost":
        return CatBoostClassifier(
            iterations=settings["iterations"],
            depth=settings["depth"],
            learning_rate=0.05,
            l2_leaf_reg=5,
            thread_count=cfg["threads"],
            random_seed=cfg["seed"],
            task_type="CPU",
            verbose=False,
            allow_writing_files=False,
        )
    if family == "lightgbm":
        return LGBMClassifier(
            n_estimators=settings["iterations"],
            num_leaves=settings["leaves"],
            max_depth=6,
            learning_rate=0.05,
            min_child_samples=40,
            reg_lambda=5,
            verbosity=-1,
            deterministic=True,
            force_col_wise=True,
            n_jobs=cfg["threads"],
            **common,
        )
    if family == "mlp":
        return MLPClassifier(
            hidden_layer_sizes=tuple(settings["hidden"]),
            max_iter=settings["epochs"],
            alpha=0.05,
            batch_size=256,
            learning_rate_init=0.001,
            early_stopping=False,  # No random window validation across the same engines.
            **common,
        )
    raise ValueError("Unapproved model family")


def fit_pipeline(family, settings, cfg, x, y):
    pipeline = make_pipeline(
        VarianceThreshold(1e-10), StandardScaler(), estimator(family, settings, cfg)
    )
    with threadpool_limits(limits=cfg["threads"]), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        pipeline.fit(x, y)
    return pipeline, [str(item.message) for item in caught]


def save_pipeline(folder, family, pipeline, names):
    folder.mkdir(parents=True, exist_ok=True)
    model = pipeline[-1]
    preprocessing = dict(
        names=names,
        kept_indices=np.flatnonzero(pipeline[0].get_support()).tolist(),
        mean=pipeline[1].mean_.tolist(),
        scale=pipeline[1].scale_.tolist(),
    )
    write_json(folder / "preprocessing.json", preprocessing)
    if family in {"baseline_xgboost", "xgboost"}:
        model.save_model(folder / "model.json")
        filename = "model.json"
    elif family == "catboost":
        model.save_model(str(folder / "model.json"), format="json")
        filename = "model.json"
    elif family == "lightgbm":
        model.booster_.save_model(str(folder / "model.txt"))
        filename = "model.txt"
    elif family == "mlp":
        write_json(
            folder / "model.json",
            dict(
                weights=[v.tolist() for v in model.coefs_],
                biases=[v.tolist() for v in model.intercepts_],
            ),
        )
        filename = "model.json"
    else:
        raise ValueError("Unapproved native export")
    record = dict(
        family=family,
        model_file=filename,
        model_hash=file_hash(folder / filename),
        preprocessing_hash=file_hash(folder / "preprocessing.json"),
    )
    write_json(folder / "component.json", record)
    return record


def load_component(folder, threads=2):
    record = json.loads((folder / "component.json").read_text())
    for filename, expected in (
        (record["model_file"], record["model_hash"]),
        ("preprocessing.json", record["preprocessing_hash"]),
    ):
        if file_hash(folder / filename) != expected:
            raise ValueError("Component artifact integrity mismatch")
    pre = json.loads((folder / "preprocessing.json").read_text())
    family = record["family"]
    if family in {"baseline_xgboost", "xgboost"}:
        model = XGBClassifier(device="cpu", n_jobs=threads)
        model.load_model(folder / record["model_file"])

        def predict(x):
            return model.predict_proba(x)[:, 1]
    elif family == "catboost":
        model = CatBoostClassifier(thread_count=threads)
        model.load_model(str(folder / record["model_file"]), format="json")

        def predict(x):
            return model.predict_proba(x)[:, 1]
    elif family == "lightgbm":
        from lightgbm import Booster

        model = Booster(
            model_file=str(folder / record["model_file"]), params={"num_threads": threads}
        )
        predict = model.predict
    elif family == "mlp":
        model = json.loads((folder / record["model_file"]).read_text())

        def predict(x):
            for i, (weight, bias) in enumerate(zip(model["weights"], model["biases"], strict=True)):
                x = x @ np.asarray(weight) + np.asarray(bias)
                x = sigmoid(x) if i == len(model["weights"]) - 1 else np.maximum(0, x)
            return x[:, 0]
    else:
        raise ValueError("Unapproved artifact family")

    def transform_predict(x):
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(pre["names"]) or not np.isfinite(x).all():
            raise ValueError("Finite ordered feature schema required")
        return np.asarray(predict((x[:, pre["kept_indices"]] - pre["mean"]) / pre["scale"]))

    return pre["names"], transform_predict


def blend(scores, selection):
    matrix = np.column_stack([scores[name] for name in selection["components"]])
    if selection["kind"] == "stack":
        logits = np.log(np.clip(matrix, 1e-6, 1 - 1e-6) / np.clip(1 - matrix, 1e-6, 1))
        return sigmoid(logits @ np.array(selection["coefficients"]) + selection["intercept"])
    return matrix @ np.asarray(selection["weights"])


def run(root, cfg):
    if cfg["version"] != "failure-improvement-v1" or cfg["threads"] not in (1, 2, 3, 4):
        raise ValueError("Frozen bounded experiment required")
    output = root / "artifacts/failure_improvement_v1"
    if (output / "selection.json").exists():
        raise ValueError("Experiment already frozen; do not overwrite the selected bundle")
    frame, sources = development(root, cfg)
    development_frame = frame.loc[frame.role.isin(["fit", "tune"])]
    tables = {
        name: build_table(development_frame, cfg, enhanced=enhanced)
        for name, enhanced in [("basic", False), ("enhanced", True)]
    }
    keys = {"stream", "cycle", "role", "event_cycle", "rul_cycles", "failure_within_horizon"}
    candidates, oof, fitted = {}, {}, {}
    for family, configurations in cfg["models"].items():
        table = tables["basic" if family == "baseline_xgboost" else "enhanced"]
        names = [name for name in table.columns if name not in keys]
        fitting = table.loc[
            (table.role == "fit")
            & ((table.cycle - cfg["minimum_cycle"]) % cfg["fit_stride_cycles"] == 0)
        ].reset_index(drop=True)
        tuning = table.loc[table.role == "tune"].reset_index(drop=True)
        anchors = endpoint_indices(fitting, cfg)
        tune_anchors = endpoint_indices(tuning, cfg)
        x, y = fitting[names].to_numpy(), fitting.failure_within_horizon.to_numpy()
        best = None
        for settings in configurations if isinstance(configurations, list) else [configurations]:
            heldout = np.full(len(y), np.nan)
            fold_records = []
            for fold, (train, validation) in enumerate(
                GroupKFold(cfg["cross_validation_folds"]).split(x, y, fitting.stream)
            ):
                model, messages = fit_pipeline(family, settings, cfg, x[train], y[train])
                heldout[validation] = model.predict_proba(x[validation])[:, 1]
                assert not set(fitting.stream.iloc[train]) & set(fitting.stream.iloc[validation])
                fold_records.append(
                    dict(
                        fold=fold,
                        fit_engines=sorted(set(fitting.stream.iloc[train])),
                        validation_engines=sorted(set(fitting.stream.iloc[validation])),
                        warnings=messages,
                    )
                )
                print(f"{family} {settings}: group fold {fold + 1}/3 complete", flush=True)
            assessed = choose_threshold(
                y[anchors], heldout[anchors], cfg["threshold"]["minimum_precision"]
            )
            key = (assessed["f2"], assessed["average_precision"])
            if best is None or key > best[0]:
                best = (key, settings, heldout, assessed, fold_records)
        _, settings, heldout, assessed, folds = best
        pipeline, messages = fit_pipeline(family, settings, cfg, x, y)
        tune_x = tuning.iloc[tune_anchors][names].to_numpy()
        start = time.perf_counter()
        predictions = pipeline.predict_proba(tune_x)[:, 1]
        latency = (time.perf_counter() - start) * 1000 / len(tune_x)
        tune_result = choose_threshold(
            tuning.iloc[tune_anchors].failure_within_horizon,
            predictions,
            cfg["threshold"]["minimum_precision"],
        )
        component = save_pipeline(output / family, family, pipeline, names)
        _, restored = load_component(output / family, cfg["threads"])
        np.testing.assert_allclose(predictions, restored(tune_x), atol=1e-7)
        candidates[family] = dict(
            kind="single",
            components=[family],
            weights=[1.0],
            settings=settings,
            oof=assessed,
            tune=tune_result,
            folds=folds,
            warnings=messages,
            inference_ms_per_row=latency,
            artifact=component,
        )
        oof[family] = heldout
        fitted[family] = predictions
        print(
            f"{family}: tune precision={tune_result['precision']:.3f} recall={tune_result['recall']:.3f} F1={tune_result['f1']:.3f}",
            flush=True,
        )
    table = tables["enhanced"]
    fitting = table.loc[
        (table.role == "fit")
        & ((table.cycle - cfg["minimum_cycle"]) % cfg["fit_stride_cycles"] == 0)
    ].reset_index(drop=True)
    tuning = table.loc[table.role == "tune"].reset_index(drop=True)
    anchors, tune_anchors = endpoint_indices(fitting, cfg), endpoint_indices(tuning, cfg)
    components = ["xgboost", "catboost", "lightgbm"]
    best_blend = None
    for weights in product((0, 0.25, 0.5, 0.75, 1), repeat=3):
        if sum(weights) != 1 or max(weights) == 1:
            continue
        selection = dict(kind="blend", components=components, weights=list(weights))
        assessed = choose_threshold(
            fitting.failure_within_horizon.iloc[anchors],
            blend(oof, selection)[anchors],
            cfg["threshold"]["minimum_precision"],
        )
        key = (assessed["f2"], assessed["average_precision"])
        if best_blend is None or key > best_blend[0]:
            best_blend = (key, selection, assessed)
    _, selection, assessed = best_blend
    candidates["soft_vote"] = selection | dict(
        oof=assessed,
        tune=choose_threshold(
            tuning.failure_within_horizon.iloc[tune_anchors],
            blend(fitted, selection),
            cfg["threshold"]["minimum_precision"],
        ),
    )
    matrix = np.column_stack([oof[name] for name in components])
    logits = np.log(np.clip(matrix, 1e-6, 1 - 1e-6) / np.clip(1 - matrix, 1e-6, 1))
    stacker = LogisticRegression(C=1, random_state=cfg["seed"], max_iter=1000).fit(
        logits, fitting.failure_within_horizon
    )
    selection = dict(
        kind="stack",
        components=components,
        coefficients=stacker.coef_[0].tolist(),
        intercept=float(stacker.intercept_[0]),
    )
    candidates["stack"] = selection | dict(
        tune=choose_threshold(
            tuning.failure_within_horizon.iloc[tune_anchors],
            blend(fitted, selection),
            cfg["threshold"]["minimum_precision"],
        ),
        meta_fit_role="out_of_fold_fit_engines_only",
    )
    # Simpler models win exact ties; no final or calibration outcomes influence this decision.
    chosen = max(
        candidates,
        key=lambda name: (
            candidates[name]["tune"]["f2"],
            candidates[name]["tune"]["average_precision"],
            -len(candidates[name]["components"]),
        ),
    )
    groups = {
        role: sorted(set(frame.loc[frame.role == role, "stream"]))
        for role in ("fit", "tune", "calibration")
    }
    selection = dict(
        version=cfg["version"],
        experiment_hash=content_hash(cfg),
        chosen=chosen,
        candidate=candidates[chosen],
        candidates=candidates,
        source_hashes=sources,
        groups=groups,
        roles_disjoint=True,
        final_test_accessed=False,
        legacy_fd001_test_used_for_selection=False,
        data_provenance=content_hash({k: v.to_dict("list") for k, v in tables.items()}),
    )
    selection["selection_hash"] = content_hash(selection)
    write_json(output / "selection.json", selection)
    write_json(root / "docs/exports/failure_improvement_tournament.json", selection)
    print(f"Frozen selection: {chosen} {candidates[chosen]['tune']}", flush=True)
    return selection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/failure_improvement.yaml")
    args = parser.parse_args()
    run(ROOT, yaml.safe_load((ROOT / args.config).read_text()))


if __name__ == "__main__":
    main()
