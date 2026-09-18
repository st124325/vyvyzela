"""Кодирование и декодирование растровых масок в RLE формата соревнования.

Правила из постановки: пиксели нумеруются построчно слева направо и сверху
вниз, начиная с единицы; пары «начальный пиксель — длина» идут по возрастанию;
серии не пересекаются и не соприкасаются; выход за пределы чипа не допускается.
"""

from __future__ import annotations

import numpy as np


def encode_rle(mask: np.ndarray) -> str:
    """Бинарная маска -> строка RLE. Пустая маска даёт пустую строку."""
    flat = np.asarray(mask).reshape(-1)
    if flat.dtype != bool:
        flat = flat != 0
    if not flat.any():
        return ""
    # Границы серий ищем по изменению значения; padding нулями закрывает края.
    padded = np.concatenate(([False], flat, [False]))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    starts = changes[0::2] + 1  # нумерация пикселей с единицы
    ends = changes[1::2] + 1
    lengths = ends - starts
    return " ".join(f"{int(s)} {int(l)}" for s, l in zip(starts, lengths))


def decode_rle(rle: str, shape: tuple[int, int]) -> np.ndarray:
    """Строка RLE -> бинарная маска заданной формы. Проверяет корректность серий."""
    height, width = shape
    mask = np.zeros(height * width, dtype=bool)
    text = (rle or "").strip()
    if not text:
        return mask.reshape(shape)
    values = text.split()
    if len(values) % 2 != 0:
        raise ValueError("RLE должен содержать чётное число чисел (пары «старт длина»)")
    numbers = np.asarray(values, dtype=np.int64)
    starts, lengths = numbers[0::2], numbers[1::2]
    if np.any(starts < 1):
        raise ValueError("нумерация пикселей начинается с единицы")
    if np.any(lengths < 1):
        raise ValueError("длина серии должна быть положительной")
    if np.any(starts[1:] <= starts[:-1]):
        raise ValueError("пары должны идти по возрастанию начального пикселя")
    ends = starts + lengths
    if np.any(ends[:-1] > starts[1:]):
        raise ValueError("серии не должны пересекаться")
    if ends[-1] - 1 > height * width:
        raise ValueError("серия выходит за пределы чипа")
    for start, length in zip(starts, lengths):
        mask[start - 1:start - 1 + length] = True
    return mask.reshape(shape)


def rle_pixel_count(rle: str) -> int:
    """Число пикселей в серии без разворачивания маски (используется в проверках)."""
    values = (rle or "").split()
    if not values:
        return 0
    return int(np.asarray(values[1::2], dtype=np.int64).sum())
