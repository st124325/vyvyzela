"""Обучение моделей и калибровка порогов на обучающей части набора.

Обучение пиксельное: из каждого чипа берутся пиксели-кандидаты (для модуля
активного горения) или пиксели контура и равный объём фона (для модуля степени
поражения). Признаки те же самые функции, что вызываются на инференсе, поэтому
расхождения train/inference по определению нет.

Разбиение на обучение и валидацию идёт по пожарам (fire_event_id), а если
колонка недоступна — по чипам: чипы одного пожара нельзя разводить по разным
частям, иначе валидация переоценивает качество.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Config
from .io.chips import ChipDataset
from .io.labels import load_mask
from .modules import active_fire, burn_severity

LOGGER = logging.getLogger(__name__)


@dataclass
class Sample:
    """Матрица признаков и метки, собранные по набору чипов."""

    features: np.ndarray
    labels: np.ndarray
    groups: np.ndarray

    def __len__(self) -> int:
        return int(self.features.shape[0])


def _group_of(dataset: ChipDataset, chip_id: str) -> str:
    meta = dataset.meta_for(chip_id)
    event = meta.get("fire_event_id")
    if event is None or (isinstance(event, float) and np.isnan(event)):
        return chip_id
    return str(event)


def collect_active_fire(dataset: ChipDataset, config: Config, rng: np.random.Generator,
                        background_per_chip: int = 2000, max_chips: int | None = None) -> Sample:
    """Выборка для модуля 1: кандидаты, эталонные пиксели горения и фон."""
    features, labels, groups = [], [], []
    chip_ids = dataset.chip_ids("af")[:max_chips]
    for chip_id in chip_ids:
        truth = load_mask(dataset, chip_id)
        if truth is None:
            continue
        chip = dataset.load(chip_id)
        layers = active_fire.compute_features(chip, config)
        positive = (truth > 0) & layers["valid"]
        # Кандидаты — это и есть трудные отрицательные примеры: техногенные
        # аномалии, блик и нагретый грунт проходят первый порог наравне с огнём.
        selection = layers["candidates"] | positive
        if background_per_chip > 0:
            pool = np.flatnonzero((layers["valid"] & ~selection).reshape(-1))
            if pool.size:
                picked = rng.choice(pool, size=min(background_per_chip, pool.size), replace=False)
                flat = selection.reshape(-1).copy()
                flat[picked] = True
                selection = flat.reshape(selection.shape)
        if not selection.any():
            continue
        features.append(active_fire.stack_features(layers, selection))
        labels.append(positive[selection].astype(np.int8))
        groups.extend([_group_of(dataset, chip_id)] * int(selection.sum()))
    if not features:
        raise RuntimeError("не собрано ни одного примера AF: нет эталонных масок в наборе")
    return Sample(np.concatenate(features), np.concatenate(labels), np.asarray(groups))


def collect_burn_severity(dataset: ChipDataset, config: Config, rng: np.random.Generator,
                          max_px_per_chip: int = 60000, max_chips: int | None = None) -> Sample:
    """Выборка для модуля 2: все пиксели гари и сопоставимый объём фона."""
    features, labels, groups = [], [], []
    for chip_id in dataset.chip_ids("bs")[:max_chips]:
        truth = load_mask(dataset, chip_id)
        if truth is None:
            continue
        chip = dataset.load(chip_id)
        layers = burn_severity.compute_features(chip, config)
        burned = truth > 0
        selection = burned.copy()
        background_pool = np.flatnonzero((~burned).reshape(-1))
        quota = min(max(int(burned.sum()), 1000), max_px_per_chip)
        if background_pool.size:
            picked = rng.choice(background_pool, size=min(quota, background_pool.size), replace=False)
            flat = selection.reshape(-1).copy()
            flat[picked] = True
            selection = flat.reshape(selection.shape)
        if not selection.any():
            continue
        features.append(burn_severity.stack_features(layers, selection))
        labels.append(truth[selection].astype(np.int8))
        groups.extend([_group_of(dataset, chip_id)] * int(selection.sum()))
    if not features:
        raise RuntimeError("не собрано ни одного примера BS: нет эталонных масок в наборе")
    return Sample(np.concatenate(features), np.concatenate(labels), np.asarray(groups))


def split_by_group(sample: Sample, rng: np.random.Generator, validation_share: float = 0.25
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Индексы обучения и валидации, разведённые по пожарам."""
    unique = np.unique(sample.groups)
    if unique.size < 2:
        indices = np.arange(len(sample))
        return indices, indices[:0]
    shuffled = rng.permutation(unique)
    validation_size = max(1, int(round(validation_share * unique.size)))
    validation_groups = set(shuffled[:validation_size].tolist())
    is_validation = np.isin(sample.groups, list(validation_groups))
    return np.flatnonzero(~is_validation), np.flatnonzero(is_validation)


def _classifier(seed: int, class_weight):
    from sklearn.ensemble import HistGradientBoostingClassifier

    # Градиентный бустинг по пиксельным признакам: устойчив к разномасштабным
    # входам, не требует нормировки и обучается на десятках миллионов строк.
    return HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.1, max_leaf_nodes=31,
        l2_regularization=1.0, min_samples_leaf=50,
        class_weight=class_weight, random_state=seed)


def train_model(sample: Sample, seed: int, balanced: bool) -> tuple[object, dict]:
    """Обучает классификатор и возвращает его вместе со сводкой по валидации."""
    from sklearn.metrics import f1_score, jaccard_score

    rng = np.random.default_rng(seed)
    train_index, validation_index = split_by_group(sample, rng)
    model = _classifier(seed, "balanced" if balanced else None)
    model.fit(sample.features[train_index], sample.labels[train_index])

    summary = {"train_px": int(train_index.size), "val_px": int(validation_index.size),
               "classes": sorted(set(sample.labels.tolist()))}
    if validation_index.size:
        predicted = model.predict(sample.features[validation_index])
        truth = sample.labels[validation_index]
        if set(np.unique(sample.labels).tolist()) <= {0, 1}:
            summary["val_f1"] = round(float(f1_score(truth, predicted, zero_division=1)), 4)
        else:
            per_class = jaccard_score(truth, predicted, labels=[1, 2, 3], average=None, zero_division=1)
            summary["val_iou_sev"] = [round(float(v), 4) for v in per_class]
            summary["val_miou_sev"] = round(float(np.mean(per_class)), 4)
    return model, summary


def calibrate_thresholds(dataset: ChipDataset, config: Config, max_chips: int | None = None
                         ) -> dict[str, dict[str, float]]:
    """Подбирает пороги dNBR по типам покрова как квантили внутри классов эталона.

    Границей между классами берётся середина между верхним квантилем нижнего
    класса и нижним квантилем верхнего — это воспроизводит логику построения
    региональной шкалы вместо переноса лесных порогов USGS на степь и пашню.
    """
    groups = config.require("burn_severity.landcover_groups")
    collected: dict[str, dict[int, list[np.ndarray]]] = {
        name: {0: [], 1: [], 2: [], 3: []} for name in groups}

    for chip_id in dataset.chip_ids("bs")[:max_chips]:
        truth = load_mask(dataset, chip_id)
        if truth is None:
            continue
        layers = burn_severity.compute_features(dataset.load(chip_id), config)
        codes = layers["landcover"].astype(np.int32)
        difference = layers["dnbr"]
        valid = layers["_optical_valid"] & ~layers["_water"]
        for name, classes in groups.items():
            in_group = np.isin(codes, [int(c) for c in classes]) & valid
            for severity in (0, 1, 2, 3):
                values = difference[in_group & (truth == severity)]
                if values.size:
                    collected[name][severity].append(values.astype(np.float32))

    result: dict[str, dict[str, float]] = {}
    for name, per_class in collected.items():
        merged = {cls: np.concatenate(chunks) for cls, chunks in per_class.items() if chunks}
        if len(merged) < 2:
            continue
        thresholds = {}
        for lower, upper, key in ((0, 1, "t1"), (1, 2, "t2"), (2, 3, "t3")):
            if lower in merged and upper in merged:
                border = 0.5 * (float(np.quantile(merged[lower], 0.99))
                                + float(np.quantile(merged[upper], 0.05)))
                thresholds[key] = round(border, 4)
        if len(thresholds) == 3 and thresholds["t1"] < thresholds["t2"] < thresholds["t3"]:
            result[name] = thresholds
        else:
            LOGGER.warning("группа %s: пороги не монотонны (%s) — оставлены значения конфигурации",
                           name, thresholds)
    return result


def save_model(model, path: str | Path) -> Path:
    import joblib

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path, compress=3)
    return path
