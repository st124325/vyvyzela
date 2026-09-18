"""Спектральные индексы и локальная (контекстная) статистика.

Индексы считаются в отражении на поверхности; деление на нулевую сумму каналов
даёт NaN, поэтому знаменатель защищён. Контекстная статистика — основа теста
активного горения: пиксель сравнивается не с абсолютным порогом, а с фоном
в скользящем окне, из которого исключены невалидные пиксели и кандидаты.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

EPS = 1e-6


def normalized_difference(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b) с защитой от нулевого знаменателя."""
    denominator = first + second
    result = np.zeros_like(first, dtype=np.float32)
    usable = np.abs(denominator) > EPS
    result[usable] = (first[usable] - second[usable]) / denominator[usable]
    return result


def nbr(nir: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """NBR = (B8A - B12) / (B8A + B12)."""
    return normalized_difference(nir, swir2)


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    return normalized_difference(nir, red)


def nbr2(swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """NBR2 = (B11 - B12) / (B11 + B12) — чувствителен к сухому остатку и золе."""
    return normalized_difference(swir1, swir2)


def dnbr(nbr_pre: np.ndarray, nbr_post: np.ndarray) -> np.ndarray:
    return nbr_pre - nbr_post


def rdnbr(delta: np.ndarray, nbr_pre: np.ndarray) -> np.ndarray:
    """RdNBR = dNBR / sqrt(|NBR(до)|). Осмыслен только там, где биомасса есть."""
    denominator = np.sqrt(np.maximum(np.abs(nbr_pre), EPS))
    return delta / denominator


def context_stats(values: np.ndarray, valid: np.ndarray, size: int
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Среднее, стандартное отклонение, среднее абсолютное отклонение и число
    валидных пикселей фона в окне ``size`` × ``size``.

    Считается через скользящие суммы (uniform_filter), поэтому стоимость не
    зависит от размера окна. Пиксели с valid == False в статистику не входят.
    """
    weights = valid.astype(np.float32)
    data = np.where(valid, values, 0.0).astype(np.float32)
    area = float(size * size)

    count = ndimage.uniform_filter(weights, size=size, mode="nearest") * area
    total = ndimage.uniform_filter(data, size=size, mode="nearest") * area
    total_sq = ndimage.uniform_filter(data * data, size=size, mode="nearest") * area

    safe_count = np.maximum(count, 1.0)
    mean = total / safe_count
    variance = np.maximum(total_sq / safe_count - mean * mean, 0.0)
    std = np.sqrt(variance)

    deviation = np.where(valid, np.abs(values - mean), 0.0).astype(np.float32)
    mad = ndimage.uniform_filter(deviation, size=size, mode="nearest") * area / safe_count

    mean = np.where(count > 0, mean, np.nan)
    return mean.astype(np.float32), std.astype(np.float32), mad.astype(np.float32), count.astype(np.float32)
