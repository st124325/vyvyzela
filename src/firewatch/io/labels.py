"""Чтение эталонных масок обучающей части.

Маски ищутся двумя способами: растр ``<chip_id>_mask.*`` рядом с чипом или
таблица в формате submission.csv (колонки chip_id, class_id, rle). Второй
вариант удобен для проверки решения на отложенной выборке.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .chips import ChipDataset, read_raster
from .rle import decode_rle

MASK_SUFFIXES = ("_mask", "_label", "_target", "_gt")


def find_mask_file(root: Path, chip_id: str) -> Path | None:
    for suffix in MASK_SUFFIXES:
        for extension in (".tif", ".tiff", ".npy", ".npz"):
            matches = sorted(root.rglob(f"{chip_id}{suffix}{extension}"))
            if matches:
                return matches[0]
    return None


def load_mask(dataset: ChipDataset, chip_id: str) -> np.ndarray | None:
    """Эталонная маска чипа: 0/1 для AF и 0–3 для BS."""
    path = find_mask_file(dataset.root, chip_id)
    if path is None:
        return None
    array = read_raster(path)
    return array[0].astype(np.uint8)


def load_truth_table(path: str | Path, shapes: dict[str, tuple[int, int]]) -> dict[str, np.ndarray]:
    """Разворачивает таблицу RLE в маски по чипам."""
    frame = pd.read_csv(path, dtype={"chip_id": str}, keep_default_na=False)
    masks: dict[str, np.ndarray] = {}
    for chip_id, class_id, rle in frame[["chip_id", "class_id", "rle"]].itertuples(index=False, name=None):
        chip_id = str(chip_id)
        shape = shapes.get(chip_id)
        if shape is None:
            continue
        mask = masks.setdefault(chip_id, np.zeros(shape, dtype=np.uint8))
        mask[decode_rle("" if pd.isna(rle) else str(rle), shape)] = int(class_id)
    return masks
