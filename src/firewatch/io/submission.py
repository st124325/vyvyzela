"""Чтение шаблона sample_submission.csv, запись и проверка файла ответа."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .rle import decode_rle, encode_rle

SUBMISSION_COLUMNS = ["chip_id", "class_id", "rle"]


def read_sample_submission(path: str | Path) -> pd.DataFrame:
    """Шаблон ответа: пары (chip_id, class_id), задающие обязательный состав строк."""
    frame = pd.read_csv(path, dtype={"chip_id": str})
    missing = [c for c in ("chip_id", "class_id") if c not in frame.columns]
    if missing:
        raise ValueError(f"в шаблоне нет колонок: {', '.join(missing)}")
    frame["class_id"] = frame["class_id"].astype(int)
    return frame[[c for c in SUBMISSION_COLUMNS if c in frame.columns]]


def masks_to_rows(chip_id: str, class_masks: Mapping[int, np.ndarray]) -> list[dict]:
    """Маски классов одного чипа -> строки файла ответа."""
    return [{"chip_id": chip_id, "class_id": int(cls), "rle": encode_rle(mask)}
            for cls, mask in sorted(class_masks.items())]


def write_submission(rows: Iterable[Mapping], template: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """Собирает файл ответа строго по шаблону.

    Строки, которых не хватает (например, чип не удалось обработать), добавляются
    с пустым RLE — формат требует полного совпадения набора пар с шаблоном
    и запрещает пропуски и NaN.
    """
    predicted = pd.DataFrame(list(rows), columns=SUBMISSION_COLUMNS)
    if not predicted.empty:
        predicted["chip_id"] = predicted["chip_id"].astype(str)
        predicted["class_id"] = predicted["class_id"].astype(int)
        predicted = predicted.drop_duplicates(subset=["chip_id", "class_id"], keep="last")
    keys = template[["chip_id", "class_id"]].copy()
    merged = keys.merge(predicted, on=["chip_id", "class_id"], how="left")
    merged["rle"] = merged["rle"].fillna("").astype(str)
    path = Path(path)
    if str(path.parent):
        path.parent.mkdir(parents=True, exist_ok=True)
    # Формат строки задан примером из постановки: chip_id и class_id без кавычек,
    # RLE — всегда в двойных кавычках, в том числе пустой ("").
    bad_ids = [c for c in merged["chip_id"].unique() if "," in c or '"' in c]
    if bad_ids:
        raise ValueError(f"идентификаторы чипов содержат запятую или кавычку: {bad_ids[:3]}")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(",".join(SUBMISSION_COLUMNS) + "\n")
        for chip_id, class_id, rle in merged.itertuples(index=False, name=None):
            handle.write(f'{chip_id},{int(class_id)},"{rle}"\n')
    return merged


def validate_submission(submission_path: str | Path,
                        template_path: str | Path,
                        shapes: Mapping[str, tuple[int, int]] | None = None) -> list[str]:
    """Полная проверка файла ответа. Возвращает список найденных нарушений."""
    problems: list[str] = []
    submission = pd.read_csv(submission_path, dtype={"chip_id": str}, keep_default_na=False)
    template = read_sample_submission(template_path)

    if list(submission.columns) != SUBMISSION_COLUMNS:
        problems.append(f"ожидались колонки {SUBMISSION_COLUMNS}, получены {list(submission.columns)}")
        return problems

    submission["class_id"] = submission["class_id"].astype(int)
    sub_keys = set(map(tuple, submission[["chip_id", "class_id"]].to_numpy()))
    tpl_keys = set(map(tuple, template[["chip_id", "class_id"]].to_numpy()))
    if len(sub_keys) != len(submission):
        problems.append("в файле есть повторяющиеся пары (chip_id, class_id)")
    if sub_keys != tpl_keys:
        missing = sorted(tpl_keys - sub_keys)[:5]
        extra = sorted(sub_keys - tpl_keys)[:5]
        if missing:
            problems.append(f"нет строк шаблона, например: {missing}")
        if extra:
            problems.append(f"есть лишние строки, например: {extra}")
    if submission["rle"].isna().any():
        problems.append("встречены пропуски (NaN) в колонке rle")

    for chip_id, group in submission.groupby("chip_id"):
        shape = (shapes or {}).get(chip_id)
        coverage = np.zeros(shape[0] * shape[1], dtype=np.int16) if shape else None
        for _, row in group.iterrows():
            text = str(row["rle"]).strip()
            if text.lower() == "nan":
                problems.append(f"{chip_id}: значение NaN вместо строки RLE")
                continue
            if not text:
                continue
            try:
                values = np.asarray(text.split(), dtype=np.int64)
            except ValueError:
                problems.append(f"{chip_id}: RLE содержит нечисловые значения")
                continue
            if len(values) % 2:
                problems.append(f"{chip_id}: нечётное число чисел в RLE")
                continue
            if coverage is None:
                continue
            try:
                mask = decode_rle(text, shape)
            except ValueError as error:
                problems.append(f"{chip_id}: {error}")
                continue
            coverage += mask.reshape(-1).astype(np.int16)
        if coverage is not None and coverage.max(initial=0) > 1:
            overlap = int((coverage > 1).sum())
            problems.append(f"{chip_id}: маски классов пересекаются в {overlap} пикселях")
    return problems
