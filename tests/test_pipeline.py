"""Сквозной прогон инференса на синтетическом наборе."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from firewatch.config import load_config
from firewatch.io.submission import validate_submission
from firewatch.metrics import evaluate_files
from firewatch.pipeline import run_inference

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _shapes_and_kinds(dataset):
    chip_ids = dataset.chip_ids()
    shapes = {cid: dataset.shape_of(cid) for cid in chip_ids}
    kinds = {cid: ("af" if cid.startswith("AF") else "bs") for cid in chip_ids}
    return shapes, kinds


def test_inference_produces_valid_submission(tmp_path, synthetic_dir, dataset, config):
    output = tmp_path / "submission.csv"
    summary = run_inference(synthetic_dir, output, config, jobs=1)

    template = synthetic_dir / "sample_submission.csv"
    expected_rows = len(pd.read_csv(template))
    assert summary["rows"] == expected_rows
    assert summary["failed_chips"] == 0

    shapes, _ = _shapes_and_kinds(dataset)
    assert not validate_submission(output, template, shapes)


def test_inference_is_reproducible(tmp_path, synthetic_dir, config):
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    run_inference(synthetic_dir, first, config, jobs=1)
    run_inference(synthetic_dir, second, config, jobs=2)
    # Требование постановки: повтор на тех же данных не меняет результат.
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_score_on_synthetic_data_is_reasonable(tmp_path, synthetic_dir, dataset, config):
    output = tmp_path / "submission.csv"
    run_inference(synthetic_dir, output, config, jobs=1)
    shapes, kinds = _shapes_and_kinds(dataset)
    report = evaluate_files(output, synthetic_dir / "truth.csv", shapes, kinds)
    # Синтетика построена по тем же физическим предпосылкам, что и правила,
    # поэтому проверяется только отсутствие грубых сбоев, не уровень качества.
    assert report.score > 0.5
    assert report.iou_burn > 0.4


def test_chip_with_broken_files_does_not_break_the_run(tmp_path, synthetic_dir, config):
    """Испорченный чип даёт пустые строки, а не падение всего инференса."""
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in ("meta.csv", "sample_submission.csv"):
        (broken / name).write_text((synthetic_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
    (broken / "af").mkdir()
    chip_id = pd.read_csv(broken / "meta.csv")["chip_id"].iloc[0]
    (broken / "af" / f"{chip_id}.tif").write_bytes(b"not a raster")

    output = tmp_path / "submission.csv"
    summary = run_inference(broken, output, config, jobs=1)
    assert summary["failed_chips"] >= 1
    written = pd.read_csv(output, keep_default_na=False)
    assert not written["rle"].isna().any()


@pytest.mark.parametrize("jobs", [1, 2])
def test_cli_entry_point_exits_with_zero(tmp_path, synthetic_dir, jobs):
    output = tmp_path / f"cli_{jobs}.csv"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "inference.py"),
         "--data-dir", str(synthetic_dir), "--output", str(output), "--jobs", str(jobs)],
        cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert output.exists()
