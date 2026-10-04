import argparse
from pathlib import Path

from fleetiq_training.classification import experiment, write_json

from fleetiq_evaluation.classification import evaluate
from fleetiq_evaluation.splits import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=["failure"])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / "docs/exports").resolve()):
        raise ValueError("Evaluation evidence belongs in ignored docs/exports")
    report = evaluate(args.bundle, experiment(args.config))
    write_json(args.output, report)
    print(report["report_hash"], report["metrics"])


if __name__ == "__main__":
    main()
