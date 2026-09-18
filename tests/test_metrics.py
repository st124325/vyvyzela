"""Подсчёт метрики Score."""

from __future__ import annotations

import numpy as np
import pandas as pd

from firewatch.io.rle import encode_rle
from firewatch.metrics import Counts, evaluate_frames


def _frame(rows):
    return pd.DataFrame(rows, columns=["chip_id", "class_id", "rle"])


def test_absent_class_counts_as_one():
    # Класса нет ни в эталоне, ни в предсказании — величина принимается за 1.
    assert Counts().iou == 1.0
    assert Counts().f1 == 1.0


def test_class_present_only_in_truth_gets_zero_iou():
    counts = Counts()
    counts.update(np.zeros((4, 4), bool), np.ones((4, 4), bool))
    assert counts.iou == 0.0


def test_perfect_prediction_gives_score_one():
    shape = (8, 8)
    fire = np.zeros(shape, bool)
    fire[2, 2] = True
    severity = np.zeros(shape, np.uint8)
    severity[0:2, :] = 1
    severity[2:4, :] = 2
    severity[4:6, :] = 3

    rows = [("AF_1", 1, encode_rle(fire))]
    rows += [("BS_1", cls, encode_rle(severity == cls)) for cls in (1, 2, 3)]
    frame = _frame(rows)
    report = evaluate_frames(frame, frame, {"AF_1": shape, "BS_1": shape},
                             {"AF_1": "af", "BS_1": "bs"})
    assert report.score == 1.0


def test_score_uses_micro_averaging_across_chips():
    shape = (4, 4)
    truth_a = np.zeros(shape, bool)
    truth_a[0, 0] = True
    truth_b = np.zeros(shape, bool)
    truth_b[1, 1] = True
    predicted = _frame([("AF_1", 1, encode_rle(truth_a)), ("AF_2", 1, "")])
    truth = _frame([("AF_1", 1, encode_rle(truth_a)), ("AF_2", 1, encode_rle(truth_b))])
    report = evaluate_frames(predicted, truth, {"AF_1": shape, "AF_2": shape},
                             {"AF_1": "af", "AF_2": "af"})
    # Общий пул: TP = 1, FN = 1, FP = 0 -> F1 = 2/3
    assert report.af.tp == 1 and report.af.fn == 1 and report.af.fp == 0
    assert abs(report.f1_af - 2 / 3) < 1e-9
