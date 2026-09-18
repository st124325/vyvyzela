"""Расчётные модули на синтетических чипах."""

from __future__ import annotations

import numpy as np

from firewatch.modules import active_fire, burn_severity
from firewatch.modules.burn_severity import _landcover_thresholds

LC_BUILT = 50


def test_active_fire_finds_fire_pixels_and_keeps_precision(dataset, config):
    found, truth_total, false_positives = 0, 0, 0
    for chip_id in dataset.chip_ids("af"):
        from firewatch.io.labels import load_mask

        truth = load_mask(dataset, chip_id)
        result = active_fire.detect(dataset.load(chip_id), config)
        found += int(np.count_nonzero(result.mask & (truth > 0)))
        truth_total += int(np.count_nonzero(truth))
        false_positives += int(np.count_nonzero(result.mask & (truth == 0)))
    assert truth_total > 0
    # Синтетика проверяет работоспособность, а не качество: важно, что очаги
    # находятся и что ложных срабатываний заметно меньше, чем верных.
    assert found > 0
    assert false_positives < truth_total


def test_technogenic_hotspots_on_builtup_are_filtered(dataset, config):
    for chip_id in dataset.chip_ids("af"):
        chip = dataset.load(chip_id)
        result = active_fire.detect(chip, config)
        landcover = chip.named_band("aux", config.require("data.af_aux_bands"), "landcover")
        assert not np.any(result.mask & (landcover.astype(int) == LC_BUILT))


def test_burn_severity_classes_are_disjoint_and_within_range(dataset, config):
    for chip_id in dataset.chip_ids("bs"):
        result = burn_severity.segment(dataset.load(chip_id), config)
        assert result.severity.min() >= 0 and result.severity.max() <= 3
        masks = result.class_masks()
        overlap = masks[1].astype(int) + masks[2].astype(int) + masks[3].astype(int)
        assert overlap.max() <= 1
        assert result.burned.sum() == sum(int(m.sum()) for m in masks.values())


def test_thresholds_depend_on_landcover(config):
    # Лес идёт по шкале USGS, травяной покров — по региональной калибровке:
    # одинаковое воздействие в степи даёт меньший dNBR.
    landcover = np.array([[10, 30, 40]], dtype=np.float32)
    t1, t2, t3, cropland = _landcover_thresholds(landcover, config)
    assert t1[0, 0] > t1[0, 1]
    assert t3[0, 0] > t3[0, 1]
    assert cropland.tolist() == [[False, False, True]]


def test_clouded_pixels_do_not_break_segmentation(dataset, config):
    for chip_id in dataset.chip_ids("bs"):
        chip = dataset.load(chip_id)
        features = burn_severity.compute_features(chip, config)
        assert not features["_optical_valid"].all(), "в синтетике есть облачные пиксели"
        result = burn_severity.segment(chip, config)
        assert result.severity.shape == chip.shape
