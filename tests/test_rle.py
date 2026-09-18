"""Кодирование и декодирование RLE."""

from __future__ import annotations

import numpy as np
import pytest

from firewatch.io.rle import decode_rle, encode_rle, rle_pixel_count


def test_empty_mask_encodes_to_empty_string():
    assert encode_rle(np.zeros((8, 8), dtype=bool)) == ""


def test_round_trip_preserves_mask():
    rng = np.random.default_rng(0)
    mask = rng.random((37, 53)) > 0.8
    assert np.array_equal(decode_rle(encode_rle(mask), mask.shape), mask)


def test_encoding_matches_example_from_task_statement():
    # Пример постановки: строка 3, столбцы 4–5 и строка 5, столбцы 2–4 на сетке 8×8.
    mask = np.zeros((8, 8), dtype=bool)
    mask[2, 3:5] = True
    mask[4, 1:4] = True
    assert encode_rle(mask) == "20 2 34 3"


def test_runs_are_sorted_and_do_not_touch():
    mask = np.zeros((6, 6), dtype=bool)
    mask[0, :3] = True
    mask[0, 4] = True
    starts = [int(v) for v in encode_rle(mask).split()[0::2]]
    assert starts == sorted(starts)


def test_pixel_count_without_decoding():
    assert rle_pixel_count("20 2 34 3") == 5
    assert rle_pixel_count("") == 0


@pytest.mark.parametrize("bad, message", [
    ("1 2 3", "чётное"),
    ("0 4", "единицы"),
    ("5 2 3 2", "возрастанию"),
    ("1 4 3 2", "пересекаться"),
    ("60 10", "пределы"),
])
def test_invalid_rle_is_rejected(bad, message):
    with pytest.raises(ValueError, match=message):
        decode_rle(bad, (8, 8))
