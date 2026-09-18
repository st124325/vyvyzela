#!/usr/bin/env python3
"""Формальная проверка submission.csv перед отправкой.

Проверяются: состав колонок, полное совпадение пар (chip_id, class_id)
с шаблоном, отсутствие пропусков и NaN, корректность RLE (возрастание,
непересечение серий, выход за границы чипа) и непересечение масок разных
классов внутри чипа.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.io.chips import ChipDataset  # noqa: E402
from firewatch.io.submission import validate_submission  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка файла ответа")
    parser.add_argument("--submission", required=True)
    parser.add_argument("--sample-submission", required=True)
    parser.add_argument("--data-dir", default=None,
                        help="каталог набора: включает проверку геометрии масок по размерам чипов")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    shapes = None
    if args.data_dir:
        dataset = ChipDataset(args.data_dir, load_config(args.config))
        shapes = {cid: dataset.shape_of(cid) for cid in dataset.chip_ids()}
        shapes = {cid: shape for cid, shape in shapes.items() if shape is not None}

    problems = validate_submission(args.submission, args.sample_submission, shapes)
    if not problems:
        print("submission.csv корректен")
        return 0
    print(f"найдено нарушений: {len(problems)}")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
