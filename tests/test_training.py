"""Обучение моделей и калибровка порогов."""

from __future__ import annotations

import numpy as np
import pytest

from firewatch import training
from firewatch.models import load_models
from firewatch.pipeline import run_inference

pytest.importorskip("sklearn")


def test_samples_are_collected_with_both_classes(dataset, config):
    rng = np.random.default_rng(0)
    sample = training.collect_active_fire(dataset, config, rng, background_per_chip=200)
    assert len(sample) > 0
    assert set(np.unique(sample.labels).tolist()) == {0, 1}

    sample_bs = training.collect_burn_severity(dataset, config, rng, max_px_per_chip=5000)
    assert set(np.unique(sample_bs.labels).tolist()) <= {0, 1, 2, 3}
    assert (sample_bs.labels > 0).any()


def test_validation_split_does_not_share_groups(dataset, config):
    rng = np.random.default_rng(1)
    sample = training.collect_active_fire(dataset, config, rng, background_per_chip=100)
    train_index, validation_index = training.split_by_group(sample, rng)
    if validation_index.size:
        assert not (set(sample.groups[train_index]) & set(sample.groups[validation_index]))


def test_trained_weights_are_used_by_inference(tmp_path, dataset, synthetic_dir, config):
    rng = np.random.default_rng(2)
    sample = training.collect_active_fire(dataset, config, rng, background_per_chip=300)
    model, report = training.train_model(sample, seed=42, balanced=True)
    weights_dir = tmp_path / "weights"
    training.save_model(model, weights_dir / "af_model.joblib")
    assert report["train_px"] > 0

    af_model, bs_model = load_models(config, weights_dir)
    assert af_model is not None and bs_model is None  # обучен только модуль 1

    output = tmp_path / "submission.csv"
    summary = run_inference(synthetic_dir, output, config, weights_dir=weights_dir, jobs=1)
    assert summary["failed_chips"] == 0


def test_calibrated_thresholds_are_monotonic(dataset, config):
    thresholds = training.calibrate_thresholds(dataset, config)
    for group, values in thresholds.items():
        assert values["t1"] < values["t2"] < values["t3"], group
