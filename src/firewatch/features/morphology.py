"""Морфологическая постобработка масок.

Растровый результат порогов всегда шумный: одиночные пиксели спекла в SAR,
«дыры» на месте облачных теней внутри гари, рваный край контура. Эти операции
приводят маску к виду, пригодному для векторизации и расчёта площади.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

# 8-связность: диагональные соседи для гарей и кластеров горения существенны
CONNECTIVITY_8 = np.ones((3, 3), dtype=bool)


def remove_small_objects(mask: np.ndarray, min_size: int) -> np.ndarray:
    """Удаляет связные области площадью меньше ``min_size`` пикселей."""
    if min_size <= 1 or not mask.any():
        return mask
    labels, count = ndimage.label(mask, structure=CONNECTIVITY_8)
    if count == 0:
        return mask
    sizes = np.bincount(labels.reshape(-1))
    keep = sizes >= min_size
    keep[0] = False
    return keep[labels]


def fill_small_holes(mask: np.ndarray, max_size: int) -> np.ndarray:
    """Заполняет внутренние дыры площадью меньше ``max_size`` пикселей."""
    if max_size <= 0 or not mask.any():
        return mask
    filled = ndimage.binary_fill_holes(mask)
    holes = filled & ~mask
    if not holes.any():
        return mask
    labels, count = ndimage.label(holes, structure=CONNECTIVITY_8)
    if count == 0:
        return mask
    sizes = np.bincount(labels.reshape(-1))
    small = sizes < max_size
    small[0] = False
    return mask | small[labels]


def smooth_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Открытие с последующим закрытием — убирает зубцы, сохраняя площадь."""
    if iterations <= 0 or not mask.any():
        return mask
    opened = ndimage.binary_opening(mask, structure=CONNECTIVITY_8, iterations=iterations)
    return ndimage.binary_closing(opened, structure=CONNECTIVITY_8, iterations=iterations)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or not mask.any():
        return mask
    return ndimage.binary_dilation(mask, structure=CONNECTIVITY_8, iterations=radius)
