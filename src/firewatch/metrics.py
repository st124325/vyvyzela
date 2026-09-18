"""Метрика соревнования: Score = 0.35·F1_af + 0.35·IoU_burn + 0.30·mIoU_sev.

Все компоненты считаются микро-усреднением: TP, FP и FN суммируются по всем
чипам общего пула пикселей, и только потом берутся precision, recall, F1 и IoU.
Такой способ устойчив к тому, что доля горящих пикселей мала (0,035 % площади
обучающего набора AF).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from .io.rle import decode_rle

WEIGHT_F1_AF = 0.35
WEIGHT_IOU_BURN = 0.35
WEIGHT_MIOU_SEV = 0.30
SEVERITY_CLASSES = (1, 2, 3)


@dataclass
class Counts:
    """Счётчики общего пула пикселей."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    def update(self, predicted: np.ndarray, truth: np.ndarray) -> None:
        self.tp += int(np.count_nonzero(predicted & truth))
        self.fp += int(np.count_nonzero(predicted & ~truth))
        self.fn += int(np.count_nonzero(~predicted & truth))

    @property
    def precision(self) -> float:
        denominator = self.tp + self.fp
        return 1.0 if denominator == 0 else self.tp / denominator

    @property
    def recall(self) -> float:
        denominator = self.tp + self.fn
        return 1.0 if denominator == 0 else self.tp / denominator

    @property
    def f1(self) -> float:
        # Класса нет ни в эталоне, ни в предсказании — величина принимается за 1.
        if self.tp + self.fp + self.fn == 0:
            return 1.0
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    @property
    def iou(self) -> float:
        denominator = self.tp + self.fp + self.fn
        return 1.0 if denominator == 0 else self.tp / denominator


@dataclass
class ScoreReport:
    """Разложение итоговой метрики по компонентам."""

    af: Counts = field(default_factory=Counts)
    burn: Counts = field(default_factory=Counts)
    severity: dict[int, Counts] = field(default_factory=lambda: {c: Counts() for c in SEVERITY_CLASSES})

    @property
    def f1_af(self) -> float:
        return self.af.f1

    @property
    def iou_burn(self) -> float:
        return self.burn.iou

    @property
    def miou_sev(self) -> float:
        return float(np.mean([self.severity[c].iou for c in SEVERITY_CLASSES]))

    @property
    def score(self) -> float:
        return (WEIGHT_F1_AF * self.f1_af
                + WEIGHT_IOU_BURN * self.iou_burn
                + WEIGHT_MIOU_SEV * self.miou_sev)

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 6),
            "f1_af": round(self.f1_af, 6),
            "precision_af": round(self.af.precision, 6),
            "recall_af": round(self.af.recall, 6),
            "iou_burn": round(self.iou_burn, 6),
            "miou_sev": round(self.miou_sev, 6),
            "iou_sev": {cls: round(self.severity[cls].iou, 6) for cls in SEVERITY_CLASSES},
            "counts": {
                "af": vars(self.af),
                "burn": vars(self.burn),
                "sev": {cls: vars(self.severity[cls]) for cls in SEVERITY_CLASSES},
            },
        }


def _rows_by_chip(frame: pd.DataFrame) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {}
    for chip_id, class_id, rle in frame[["chip_id", "class_id", "rle"]].itertuples(index=False, name=None):
        result.setdefault(str(chip_id), {})[int(class_id)] = "" if pd.isna(rle) else str(rle)
    return result


def evaluate_frames(prediction: pd.DataFrame,
                    truth: pd.DataFrame,
                    shapes: Mapping[str, tuple[int, int]],
                    kinds: Mapping[str, str]) -> ScoreReport:
    """Считает метрику по двум таблицам в формате submission.csv."""
    report = ScoreReport()
    predicted_rows, truth_rows = _rows_by_chip(prediction), _rows_by_chip(truth)

    for chip_id, truth_classes in truth_rows.items():
        shape = shapes.get(chip_id)
        if shape is None:
            raise KeyError(f"неизвестен размер чипа {chip_id}: нужен meta.csv или каталог данных")
        kind = kinds.get(chip_id, "af" if chip_id.lower().startswith("af") else "bs")
        predicted_classes = predicted_rows.get(chip_id, {})

        if kind == "af":
            truth_mask = decode_rle(truth_classes.get(1, ""), shape)
            predicted_mask = decode_rle(predicted_classes.get(1, ""), shape)
            report.af.update(predicted_mask, truth_mask)
            continue

        truth_burn = np.zeros(shape, dtype=bool)
        predicted_burn = np.zeros(shape, dtype=bool)
        for cls in SEVERITY_CLASSES:
            truth_mask = decode_rle(truth_classes.get(cls, ""), shape)
            predicted_mask = decode_rle(predicted_classes.get(cls, ""), shape)
            report.severity[cls].update(predicted_mask, truth_mask)
            truth_burn |= truth_mask
            predicted_burn |= predicted_mask
        report.burn.update(predicted_burn, truth_burn)
    return report


def evaluate_files(prediction_path: str | Path,
                   truth_path: str | Path,
                   shapes: Mapping[str, tuple[int, int]],
                   kinds: Mapping[str, str]) -> ScoreReport:
    read = dict(dtype={"chip_id": str}, keep_default_na=False)
    prediction = pd.read_csv(prediction_path, **read)
    truth = pd.read_csv(truth_path, **read)
    return evaluate_frames(prediction, truth, shapes, kinds)
