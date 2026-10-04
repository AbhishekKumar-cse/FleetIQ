import argparse
from pathlib import Path

from fleetiq_evaluation.splits import ROOT

from fleetiq_training.classification import experiment, train


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=["failure"])
    parser.add_argument("--model", required=True, choices=["logistic", "random_forest", "xgboost"])
    parser.add_argument("--track", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Models belong under ignored artifacts")
    manifest, report = train(
        experiment(args.config), args.output, model=args.model, track=args.track
    )
    print(manifest["model"], manifest["manifest_hash"], report["tune"])


if __name__ == "__main__":
    main()
