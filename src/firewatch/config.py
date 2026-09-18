"""Загрузка конфигурации пайплайна.

Все пороги и параметры лежат в YAML (configs/default.yaml). Точки входа
принимают путь к конфигу и, при необходимости, точечные переопределения вида
``active_fire.window=15``, чтобы эксперименты не требовали правки кода.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

# Корень репозитория: src/firewatch/config.py -> src/firewatch -> src -> корень
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "default.yaml"


class Config(dict):
    """Словарь с доступом по пути ``a.b.c`` и с разрешением относительных путей."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get_path(dotted, sentinel)
        if value is sentinel:
            raise KeyError(f"в конфигурации нет параметра {dotted!r}")
        return value

    def resolve(self, dotted: str, default: Any = None) -> Path | None:
        """Путь из конфигурации, приведённый к абсолютному относительно корня проекта."""
        raw = self.get_path(dotted, default)
        if raw in (None, ""):
            return None
        path = Path(str(raw)).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path)


def _set_dotted(target: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise TypeError(f"нельзя переопределить {dotted!r}: {part!r} не является разделом")
    node[parts[-1]] = value


def _coerce(raw: str) -> Any:
    """Строку из командной строки приводим к типу YAML (число, bool, список)."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def load_config(path: str | os.PathLike[str] | None = None,
                overrides: Iterable[str] | None = None) -> Config:
    """Читает YAML-конфигурацию и применяет переопределения ``ключ=значение``."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()
    with open(config_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data = copy.deepcopy(data)
    for item in overrides or []:
        if "=" not in item:
            raise ValueError(f"переопределение {item!r} должно иметь вид ключ=значение")
        key, raw_value = item.split("=", 1)
        _set_dotted(data, key.strip(), _coerce(raw_value.strip()))
    return Config(data)
