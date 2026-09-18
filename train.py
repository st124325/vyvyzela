#!/usr/bin/env python3
"""Точка входа обучения.

    python train.py --data-dir data/train --output-dir data/weights

Обучает модели обоих модулей на обучающей части набора и, по запросу,
пересчитывает пороги dNBR по типам земного покрова. Запуск при проверке
не обязателен: без весов инференс работает на правилах и порогах.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from firewatch import training  # noqa: E402
from firewatch.config import load_config  # noqa: E402
from firewatch.io.chips import ChipDataset  # noqa: E402
from firewatch.models import resolve_weight_path  # noqa: E402
from firewatch.utils.logging import setup_logging  # noqa: E402
from firewatch.utils.seed import set_global_seed  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Обучение моделей мониторинга пожаров",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="каталог обучающей части с эталонными масками")
    parser.add_argument("--output-dir", default="data/weights", help="куда сохранить веса")
    parser.add_argument("--config", default=None)
    parser.add_argument("--module", choices=["af", "bs", "both"], default="both")
    parser.add_argument("--max-chips", type=int, default=None, help="ограничение числа чипов (отладка)")
    parser.add_argument("--calibrate-thresholds", action="store_true",
                        help="пересчитать пороги dNBR по покрову и сохранить configs/calibrated.yaml")
    parser.add_argument("--report", default=None, help="путь для JSON-сводки обучения")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="КЛЮЧ=ЗНАЧЕНИЕ")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logging(args.verbose)
    config = load_config(args.config, args.overrides)
    seed = int(config.get_path("seed", 1234))
    set_global_seed(seed)
    rng = np.random.default_rng(seed)

    dataset = ChipDataset(args.data_dir, config)
    output_dir = Path(args.output_dir)
    summary: dict = {"data_dir": str(args.data_dir), "seed": seed}

    if args.module in ("af", "both"):
        sample = training.collect_active_fire(dataset, config, rng, max_chips=args.max_chips)
        logger.info("AF: собрано %d пикселей, из них горения %d", len(sample), int(sample.labels.sum()))
        # Доля горящих пикселей мала (0,035 % набора) — компенсируем весами классов.
        model, report = training.train_model(sample, seed, balanced=True)
        path = training.save_model(model, resolve_weight_path(config, "active_fire", output_dir))
        summary["active_fire"] = {**report, "weights": str(path)}
        logger.info("AF: веса сохранены в %s (%s)", path, report)

    if args.module in ("bs", "both"):
        sample = training.collect_burn_severity(dataset, config, rng, max_chips=args.max_chips)
        logger.info("BS: собрано %d пикселей, из них гарь %d", len(sample), int((sample.labels > 0).sum()))
        model, report = training.train_model(sample, seed, balanced=False)
        path = training.save_model(model, resolve_weight_path(config, "burn_severity", output_dir))
        summary["burn_severity"] = {**report, "weights": str(path)}
        logger.info("BS: веса сохранены в %s (%s)", path, report)

    if args.calibrate_thresholds:
        thresholds = training.calibrate_thresholds(dataset, config, max_chips=args.max_chips)
        summary["calibrated_thresholds"] = thresholds
        if thresholds:
            target = Path("configs/calibrated.yaml")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                yaml.safe_dump({"burn_severity": {"thresholds": thresholds}},
                               allow_unicode=True, sort_keys=False), encoding="utf-8")
            logger.info("пороги dNBR пересчитаны и записаны в %s", target)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
