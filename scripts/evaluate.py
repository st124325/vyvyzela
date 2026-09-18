#!/usr/bin/env python3
"""Подсчёт метрики Score по файлу ответа и эталону.

    python scripts/evaluate.py --submission submission.csv --truth data/train/truth.csv \
        --data-dir data/train
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.io.chips import ChipDataset  # noqa: E402
from firewatch.metrics import evaluate_files  # noqa: E402
from firewatch.utils.logging import setup_logging  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Метрика Score = 0.35·F1_af + 0.35·IoU_burn + 0.30·mIoU_sev")
    parser.add_argument("--submission", required=True)
    parser.add_argument("--truth", required=True, help="эталон в формате submission.csv")
    parser.add_argument("--data-dir", required=True, help="каталог набора (нужен для размеров чипов)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--json", dest="json_path", default=None, help="куда сохранить отчёт в JSON")
    args = parser.parse_args(argv)

    setup_logging()
    config = load_config(args.config)
    dataset = ChipDataset(args.data_dir, config)
    chip_ids = dataset.chip_ids()
    shapes = {cid: dataset.shape_of(cid) for cid in chip_ids}
    shapes = {cid: shape for cid, shape in shapes.items() if shape is not None}
    kinds = {cid: dataset.kind_of(cid) for cid in chip_ids}

    report = evaluate_files(args.submission, args.truth, shapes, kinds).as_dict()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
