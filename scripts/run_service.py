#!/usr/bin/env python3
"""Запуск информационно-аналитического сервиса.

    python scripts/run_service.py --catalog data/service_catalog --port 8000

Открывает веб-карту на http://localhost:8000/ и REST API на /api/*.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.utils.logging import setup_logging  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сервис мониторинга природных пожаров")
    parser.add_argument("--catalog", default=None, help="каталог сцен (по умолчанию из конфигурации)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    setup_logging()
    config = load_config(args.config)
    import uvicorn

    from firewatch.service.app import create_app

    application = create_app(args.catalog, args.config)
    uvicorn.run(application,
                host=args.host or str(config.get_path("service.host", "0.0.0.0")),
                port=args.port or int(config.get_path("service.port", 8000)),
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
