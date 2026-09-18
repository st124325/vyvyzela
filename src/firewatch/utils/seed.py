"""Фиксация случайных начальных значений.

Требование постановки: повторный запуск на тех же данных даёт метрику,
отличающуюся не более чем на 0,005. Решение детерминированное, но обучение
использует генераторы случайных чисел, поэтому seed фиксируется явно.
"""

from __future__ import annotations

import os
import random


def set_global_seed(seed: int) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # numpy обязателен для расчётов, но seed не должен падать
        pass
