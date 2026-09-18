#!/usr/bin/env python3
"""Подготовка каталога сцен для информационно-аналитического сервиса.

    python scripts/build_service_catalog.py --data-dir data/train --output data/service_catalog

Чипы прогоняются обоими модулями, результат векторизуется и сохраняется в виде
двух GeoJSON и описания сцен. Чипы без геопривязки (обезличенный тест) в каталог
не попадают — их нельзя положить на карту.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.service import catalog as catalog_module  # noqa: E402
from firewatch.utils.logging import setup_logging  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сборка каталога сервиса")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", default="data/service_catalog")
    parser.add_argument("--config", default=None)
    parser.add_argument("--weights-dir", default=None)
    parser.add_argument("--limit", type=int, default=None, help="ограничить число чипов")
    args = parser.parse_args(argv)

    setup_logging()
    summary = catalog_module.build(args.data_dir, args.output, load_config(args.config),
                                   args.weights_dir, args.limit)
    print(json.dumps({k: v for k, v in summary.items() if k != "scenes"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
