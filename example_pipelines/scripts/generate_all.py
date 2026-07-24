#!/usr/bin/env python3
"""Generate every dataset used by the bundled example pipelines."""

from __future__ import annotations

import argparse
from pathlib import Path

from example_pipelines.scripts.generate_comms import generate as generate_comms
from example_pipelines.scripts.generate_lte import generate as generate_lte


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="Parent output directory (default: example_pipelines/data)",
    )
    args = parser.parse_args()
    root = args.output.resolve()

    generated = (
        *generate_lte(root / "lte"),
        *generate_comms(root / "comms"),
    )
    for metadata_path, data_path in generated:
        print(f"Wrote {metadata_path}")
        print(f"Wrote {data_path}")
    print(f"Generated all bundled example data under {root}")


if __name__ == "__main__":
    main()
