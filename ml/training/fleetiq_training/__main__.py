import argparse
from pathlib import Path

from fleetiq_evaluation.splits import ROOT

from fleetiq_training.classification import experiment, train


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=["failure", "anomaly"])
    parser.add_argument(
        "--model",
        required=True,
        choices=["logistic", "random_forest", "xgboost", "isolation_forest"],
    )
    parser.add_argument("--track", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Models belong under ignored artifacts")
    if args.task == "anomaly":
        if args.model != "isolation_forest" or args.track != "synthetic_sensor_model":
            raise ValueError("Anomaly requires isolation_forest and synthetic_sensor_model")
        from fleetiq_training.anomaly import train_anomaly

        report = train_anomaly(experiment(args.config), args.output)
        print(report["model"], report["threshold"], report["tune"])
        return
    if args.model == "isolation_forest":
        raise ValueError("Isolation Forest is a separate anomaly task")
    manifest, report = train(
        experiment(args.config), args.output, model=args.model, track=args.track
    )
    print(manifest["model"], manifest["manifest_hash"], report["tune"])


if __name__ == "__main__":
    main()
