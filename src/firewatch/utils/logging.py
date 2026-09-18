"""Единая настройка журналирования для точек входа."""

from __future__ import annotations

import logging
import sys


def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    # Инференс может вызываться повторно в одном процессе (тесты, сервис) —
    # не размножаем обработчики.
    if not any(getattr(h, "_firewatch", False) for h in root.handlers):
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._firewatch = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    for handler in root.handlers:
        handler.setLevel(level)
    return logging.getLogger("firewatch")
