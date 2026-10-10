"""Build a separately evaluated native-hour bundle in WSL Ubuntu."""

import argparse
from pathlib import Path

from fleetiq_evaluation.splits import ROOT
from fleetiq_evaluation.synthetic import evaluate_anomaly
from fleetiq_training.classification import experiment, write_json
from fleetiq_training.synthetic_track import train


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    digest, report = train(experiment(args.config), args.output)
    write_json(
        ROOT / "docs/exports/synthetic_anomaly_evaluation.json",
        evaluate_anomaly(experiment(args.config), args.output, digest),
    )
    print(dict(approved_digest=digest, risk=report["risk"], rul=report["rul"]))


if __name__ == "__main__":
    main()
