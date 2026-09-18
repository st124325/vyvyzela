#!/usr/bin/env python3
"""Точка входа инференса.

    python inference.py --data-dir /path/to/test --output /path/to/submission.csv

Скрипт принимает каталог с тестовыми чипами в структуре выданного набора,
обрабатывает все чипы из sample_submission.csv, включая облачные, формирует
файл строго в требуемом формате и завершается с кодом 0 без интерактивного ввода.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Параллелизм берём процессами по чипам, а не потоками внутри библиотек:
# так время предсказуемо, а результат не зависит от числа потоков BLAS/OpenMP.
# Переменные выставляются до импорта numpy и scikit-learn — позже они не читаются.
for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.pipeline import run_inference  # noqa: E402
from firewatch.utils.logging import setup_logging  # noqa: E402
from firewatch.utils.seed import set_global_seed  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Инференс двухэтапного мониторинга природных пожаров",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="каталог с тестовыми чипами")
    parser.add_argument("--output", required=True, help="путь к создаваемому submission.csv")
    parser.add_argument("--config", default=None, help="YAML с параметрами (по умолчанию configs/default.yaml)")
    parser.add_argument("--sample-submission", default=None,
                        help="шаблон ответа, если он лежит вне каталога данных")
    parser.add_argument("--weights-dir", default=None, help="каталог с весами моделей")
    parser.add_argument("--no-model", action="store_true",
                        help="считать только по правилам и порогам, игнорируя веса")
    parser.add_argument("--jobs", type=int, default=0, help="число процессов (0 — по числу ядер)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="КЛЮЧ=ЗНАЧЕНИЕ",
                        help="точечное переопределение параметра конфигурации")
    parser.add_argument("--verbose", action="store_true", help="подробный журнал")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logging(args.verbose)
    config = load_config(args.config, args.overrides)
    set_global_seed(int(config.get_path("seed", 1234)))

    summary = run_inference(
        data_dir=args.data_dir,
        output=args.output,
        config=config,
        sample_submission=args.sample_submission,
        weights_dir=args.weights_dir,
        use_model=not args.no_model,
        jobs=args.jobs,
    )
    logger.info("submission.csv: %s (%d строк, %.2f с)",
                summary["output"], summary["rows"], summary["elapsed_sec"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
