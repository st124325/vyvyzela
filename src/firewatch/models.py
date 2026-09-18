"""Загрузка обученных весов.

Веса необязательны: без них оба модуля работают на физически обоснованных
правилах и калиброванных порогах, что делает инференс воспроизводимым «из
коробки». Если файлы весов есть, решение по пикселю принимает обученная модель.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config

LOGGER = logging.getLogger(__name__)


def _load(path: Path | None):
    if path is None or not path.exists():
        return None
    import joblib

    try:
        return joblib.load(path)
    except Exception as error:  # повреждённые веса не должны ронять инференс
        LOGGER.warning("не удалось загрузить веса %s (%s) — используются правила", path, error)
        return None


def resolve_weight_path(config: Config, key: str, weights_dir: str | Path | None) -> Path | None:
    """Путь к весам: каталог из командной строки имеет приоритет над конфигом."""
    configured = config.resolve(f"{key}.model.path")
    if weights_dir is None:
        return configured
    directory = Path(weights_dir)
    name = Path(configured).name if configured else f"{key}_model.joblib"
    return directory / name


def load_models(config: Config, weights_dir: str | Path | None = None, enabled: bool = True
                ) -> tuple[object | None, object | None]:
    """Возвращает пару (модель AF, модель BS); None означает работу на правилах."""
    if not enabled:
        return None, None
    active_fire = _load(resolve_weight_path(config, "active_fire", weights_dir)) \
        if config.get_path("active_fire.model.enabled", True) else None
    burn_severity = _load(resolve_weight_path(config, "burn_severity", weights_dir)) \
        if config.get_path("burn_severity.model.enabled", True) else None
    for name, model in (("активного горения", active_fire), ("степени поражения", burn_severity)):
        LOGGER.info("модуль %s: %s", name, "обученная модель" if model is not None else "правила и пороги")
    return active_fire, burn_severity
