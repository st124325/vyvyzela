"""Доступ к набору данных соревнования.

Набор выдаётся чипами: AF-чип (VIIRS I1–I5 на сетке 375 м) и BS-чип (пара
снимков Sentinel-2 «до/после», пара Sentinel-1 и вспомогательные слои на сетке
20 м). Точная раскладка файлов внутри каталога может отличаться от релиза к
релизу, поэтому загрузчик ищет файлы по chip_id рекурсивно и определяет роль по
суффиксу имени; список суффиксов и порядок каналов вынесены в конфигурацию.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import Config

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeoReference:
    """Геопривязка чипа: аффинное преобразование и система координат."""

    transform: tuple[float, float, float, float, float, float] | None
    crs: str | None

    @property
    def is_known(self) -> bool:
        return self.transform is not None and self.crs is not None


@dataclass
class Chip:
    """Растровые слои одного чипа и сопровождающие их сведения."""

    chip_id: str
    kind: str                       # "af" или "bs"
    layers: dict[str, np.ndarray]   # роль файла -> массив (каналы, высота, ширина)
    meta: dict = field(default_factory=dict)
    geo: GeoReference = field(default_factory=lambda: GeoReference(None, None))

    @property
    def shape(self) -> tuple[int, int]:
        for array in self.layers.values():
            return int(array.shape[-2]), int(array.shape[-1])
        raise ValueError(f"чип {self.chip_id} не содержит растровых слоёв")

    def band(self, role: str, index: int) -> np.ndarray | None:
        """Канал по номеру; None, если слоя нет или каналов меньше, чем нужно."""
        array = self.layers.get(role)
        if array is None or index >= array.shape[0]:
            return None
        return array[index].astype(np.float32)

    def named_band(self, role: str, mapping: Mapping[str, int], name: str) -> np.ndarray | None:
        index = mapping.get(name)
        return None if index is None else self.band(role, index)

    @property
    def gsd(self) -> float:
        """Размер пикселя на местности, м. Берётся из meta.csv, иначе из умолчания."""
        value = self.meta.get("gsd")
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return 375.0 if self.kind == "af" else 20.0
        return float(value)


def read_raster(path: Path) -> np.ndarray:
    """Чтение растра в массив (каналы, высота, ширина) независимо от формата."""
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff"):
        import rasterio

        with rasterio.open(path) as dataset:
            return dataset.read().astype(np.float32)
    if suffix == ".npy":
        array = np.load(path)
    elif suffix == ".npz":
        with np.load(path) as bundle:
            keys = list(bundle.keys())
            # Один массив — берём его; несколько — складываем по порядку ключей.
            array = bundle[keys[0]] if len(keys) == 1 else np.stack([bundle[k] for k in keys])
    else:
        raise ValueError(f"неподдерживаемый формат растра: {path.name}")
    array = np.asarray(array, dtype=np.float32)
    return array[None, ...] if array.ndim == 2 else array


def _read_georeference(path: Path) -> GeoReference:
    if path.suffix.lower() not in (".tif", ".tiff"):
        return GeoReference(None, None)
    import rasterio

    with rasterio.open(path) as dataset:
        if dataset.crs is None or dataset.transform is None or dataset.transform.is_identity:
            return GeoReference(None, None)
        return GeoReference(tuple(dataset.transform)[:6], dataset.crs.to_string())


class ChipDataset:
    """Каталог набора: перечисление чипов, чтение слоёв, доступ к meta.csv."""

    def __init__(self, root: str | Path, config: Config):
        self.root = Path(root)
        if not self.root.exists():
            raise FileNotFoundError(f"каталог с данными не найден: {self.root}")
        self.config = config
        self.meta = self._read_meta()
        self._index = self._build_index()

    # ------------------------------------------------------------------ meta
    def _read_meta(self) -> pd.DataFrame:
        for name in ("meta.csv", "metadata.csv"):
            path = self.root / name
            if path.exists():
                frame = pd.read_csv(path, dtype={"chip_id": str})
                return frame.set_index("chip_id", drop=False)
        LOGGER.warning("meta.csv не найден в %s — используются значения по умолчанию", self.root)
        return pd.DataFrame(columns=["chip_id", "kind", "width", "height", "gsd"]).set_index("chip_id")

    def meta_for(self, chip_id: str) -> dict:
        if chip_id in self.meta.index:
            row = self.meta.loc[chip_id]
            if isinstance(row, pd.DataFrame):  # дубликаты chip_id в meta.csv
                row = row.iloc[0]
            return {k: v for k, v in row.to_dict().items()}
        return {}

    @property
    def sample_submission_path(self) -> Path | None:
        for name in ("sample_submission.csv", "sample_submission.CSV"):
            path = self.root / name
            if path.exists():
                return path
        return None

    # ----------------------------------------------------------------- index
    def _role_patterns(self, kind: str) -> dict[str, Sequence[str]]:
        key = "data.af_roles" if kind == "af" else "data.bs_roles"
        return self.config.require(key)

    def _build_index(self) -> dict[str, dict[str, Path]]:
        """Сопоставляет chip_id -> {роль файла: путь}."""
        extensions = {e.lower() for e in self.config.require("data.raster_extensions")}
        files = [p for p in self.root.rglob("*") if p.is_file() and p.suffix.lower() in extensions]
        known_ids = set(self.meta.index.astype(str)) if len(self.meta) else set()

        index: dict[str, dict[str, Path]] = {}
        for path in files:
            stem = path.stem
            chip_id = self._match_chip_id(stem, known_ids)
            if chip_id is None:
                continue
            kind = self.kind_of(chip_id)
            role = self._role_of(stem[len(chip_id):].lower(), kind)
            if role is None:
                continue
            index.setdefault(chip_id, {}).setdefault(role, path)
        return index

    def _match_chip_id(self, stem: str, known_ids: set[str]) -> str | None:
        if stem in known_ids:
            return stem
        # Имя вида "<chip_id><суффикс роли>" — ищем самое длинное совпадение.
        candidates = [cid for cid in known_ids if stem.startswith(cid)]
        if candidates:
            return max(candidates, key=len)
        if known_ids:
            return None
        # meta.csv отсутствует: chip_id восстанавливаем как имя до известного суффикса.
        lowered = stem.lower()
        for kind in ("af", "bs"):
            for suffixes in self._role_patterns(kind).values():
                for suffix in suffixes:
                    if suffix and lowered.endswith(suffix):
                        return stem[: len(stem) - len(suffix)]
        return stem

    def kind_of(self, chip_id: str) -> str:
        """af или bs: из meta.csv, а при его отсутствии — по префиксу chip_id."""
        meta = self.meta_for(chip_id)
        kind = str(meta.get("kind", "")).lower()
        if kind in ("af", "bs"):
            return kind
        return "af" if chip_id.lower().startswith("af") else "bs"

    def _role_of(self, suffix: str, kind: str) -> str | None:
        patterns = self._role_patterns(kind)
        best_role, best_length = None, -1
        for role, variants in patterns.items():
            for variant in variants:
                variant = str(variant).lower()
                if suffix == variant and len(variant) > best_length:
                    best_role, best_length = role, len(variant)
        if best_role is not None:
            return best_role
        LOGGER.debug("суффикс %r не опознан (%s)", suffix, kind)
        return None

    # ------------------------------------------------------------------ chips
    def chip_ids(self, kind: str | None = None) -> list[str]:
        ids = sorted(self._index)
        if kind is None:
            return ids
        return [cid for cid in ids if self.kind_of(cid) == kind]

    def has_chip(self, chip_id: str) -> bool:
        return chip_id in self._index

    def paths_for(self, chip_id: str) -> dict[str, Path]:
        return dict(self._index.get(chip_id, {}))

    def load(self, chip_id: str) -> Chip:
        paths = self._index.get(chip_id)
        if not paths:
            raise KeyError(f"чип {chip_id} не найден в {self.root}")
        kind = self.kind_of(chip_id)
        layers = {role: read_raster(path) for role, path in paths.items()}
        main_role = "main" if kind == "af" else "post"
        anchor = paths.get(main_role) or next(iter(paths.values()))
        return Chip(chip_id=chip_id, kind=kind, layers=layers,
                    meta=self.meta_for(chip_id), geo=_read_georeference(anchor))

    def shape_of(self, chip_id: str) -> tuple[int, int] | None:
        """Размер чипа из meta.csv без чтения растра (нужен для проверки RLE)."""
        meta = self.meta_for(chip_id)
        width, height = meta.get("width"), meta.get("height")
        if width and height and not (pd.isna(width) or pd.isna(height)):
            return int(height), int(width)
        if chip_id in self._index:
            return self.load(chip_id).shape
        return None
