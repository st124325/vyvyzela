"""Формирование и проверка файла ответа."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from firewatch.io.submission import read_sample_submission, validate_submission, write_submission


@pytest.fixture()
def template(tmp_path):
    frame = pd.DataFrame([
        {"chip_id": "AF_te_000001", "class_id": 1, "rle": ""},
        {"chip_id": "BS_te_000001", "class_id": 1, "rle": ""},
        {"chip_id": "BS_te_000001", "class_id": 2, "rle": ""},
        {"chip_id": "BS_te_000001", "class_id": 3, "rle": ""},
    ])
    path = tmp_path / "sample_submission.csv"
    frame.to_csv(path, index=False)
    return path


def test_missing_rows_are_filled_with_empty_rle(tmp_path, template):
    output = tmp_path / "submission.csv"
    write_submission([{"chip_id": "AF_te_000001", "class_id": 1, "rle": "20 2"}],
                     read_sample_submission(template), output)
    written = pd.read_csv(output, keep_default_na=False)
    assert len(written) == 4
    assert set(written["rle"]) == {"20 2", ""}
    assert not validate_submission(output, template)


def test_rle_column_is_always_quoted(tmp_path, template):
    output = tmp_path / "submission.csv"
    write_submission([{"chip_id": "AF_te_000001", "class_id": 1, "rle": "20 2 34 3"}],
                     read_sample_submission(template), output)
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "chip_id,class_id,rle"
    assert 'AF_te_000001,1,"20 2 34 3"' in lines
    assert 'BS_te_000001,1,""' in lines


def test_validator_reports_missing_and_extra_rows(tmp_path, template):
    output = tmp_path / "submission.csv"
    output.write_text('chip_id,class_id,rle\nAF_te_000001,1,""\nXX_1,1,""\n', encoding="utf-8")
    problems = validate_submission(output, template)
    assert any("нет строк шаблона" in p for p in problems)
    assert any("лишние строки" in p for p in problems)


def test_validator_detects_overlapping_classes(tmp_path, template):
    output = tmp_path / "submission.csv"
    output.write_text(
        'chip_id,class_id,rle\n'
        'AF_te_000001,1,""\n'
        'BS_te_000001,1,"5 3"\n'
        'BS_te_000001,2,"6 2"\n'
        'BS_te_000001,3,""\n', encoding="utf-8")
    problems = validate_submission(output, template, shapes={"BS_te_000001": (8, 8)})
    assert any("пересекаются" in p for p in problems)


def test_validator_detects_out_of_bounds_runs(tmp_path, template):
    output = tmp_path / "submission.csv"
    output.write_text(
        'chip_id,class_id,rle\n'
        'AF_te_000001,1,"60 20"\n'
        'BS_te_000001,1,""\nBS_te_000001,2,""\nBS_te_000001,3,""\n', encoding="utf-8")
    problems = validate_submission(output, template, shapes={"AF_te_000001": (8, 8)})
    assert any("пределы" in p for p in problems)


def test_nan_values_are_not_written(tmp_path, template):
    output = tmp_path / "submission.csv"
    frame = write_submission([{"chip_id": "AF_te_000001", "class_id": 1, "rle": np.nan}],
                             read_sample_submission(template), output)
    assert not frame["rle"].isna().any()
    assert "nan" not in output.read_text(encoding="utf-8").lower()
