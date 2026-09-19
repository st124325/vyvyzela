#!/usr/bin/env python3
"""Строит truth.csv в формате submission.csv из эталонных масок train-набора.

    python scripts/build_truth.py --data-dir data/train --output data/train/truth.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.io.chips import ChipDataset  # noqa: E402
from firewatch.io.labels import load_mask  # noqa: E402
from firewatch.io.rle import encode_rle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    dataset = ChipDataset(args.data_dir, config)

    import pandas as pd

    rows = []
    missing = 0
    for chip_id in dataset.chip_ids():
        mask = load_mask(dataset, chip_id)
        if mask is None:
            missing += 1
            continue
        kind = dataset.kind_of(chip_id)
        classes = (1,) if kind == "af" else (1, 2, 3)
        for cls in classes:
            rows.append({"chip_id": chip_id, "class_id": cls, "rle": encode_rle(mask == cls)})

    pd.DataFrame(rows, columns=["chip_id", "class_id", "rle"]).to_csv(args.output, index=False)
    print(f"truth.csv: {len(rows)} строк, чипов без маски: {missing} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
