"""Сквозной инференс: каталог с чипами -> submission.csv.

Чипы независимы, поэтому обработка распараллеливается по процессам. Ошибка на
отдельном чипе не прекращает прогон: для него пишутся пустые строки, файл ответа
остаётся валидным по составу строк, а причина попадает в журнал.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .io.chips import ChipDataset
from .io.submission import masks_to_rows, read_sample_submission, write_submission
from .models import load_models
from .modules import active_fire, burn_severity

LOGGER = logging.getLogger(__name__)

# Состояние рабочих процессов: инициализируется один раз на процесс.
_WORKER: dict = {}


def process_chip(dataset: ChipDataset, chip_id: str, config: Config,
                 af_model=None, bs_model=None) -> list[dict]:
    """Строки файла ответа для одного чипа."""
    kind = dataset.kind_of(chip_id)
    chip = dataset.load(chip_id)
    if kind == "af":
        result = active_fire.detect(chip, config, model=af_model)
        return masks_to_rows(chip_id, {1: result.mask})
    result = burn_severity.segment(chip, config, model=bs_model)
    return masks_to_rows(chip_id, result.class_masks())


def _empty_rows(dataset: ChipDataset, chip_id: str) -> list[dict]:
    classes = (1,) if dataset.kind_of(chip_id) == "af" else (1, 2, 3)
    return [{"chip_id": chip_id, "class_id": cls, "rle": ""} for cls in classes]


def _init_worker(dataset: ChipDataset, config: Config, weights_dir, use_model: bool) -> None:
    from .utils.logging import setup_logging

    setup_logging()
    af_model, bs_model = load_models(config, weights_dir, enabled=use_model)
    _WORKER.update(dataset=dataset, config=config, af_model=af_model, bs_model=bs_model)


def _run_worker(chip_id: str) -> tuple[str, list[dict], str | None]:
    dataset, config = _WORKER["dataset"], _WORKER["config"]
    try:
        rows = process_chip(dataset, chip_id, config, _WORKER["af_model"], _WORKER["bs_model"])
        return chip_id, rows, None
    except Exception as error:
        return chip_id, _empty_rows(dataset, chip_id), f"{type(error).__name__}: {error}"


def resolve_jobs(requested: int | None) -> int:
    """Число процессов: 0 или None — автоматически по числу ядер."""
    if requested and requested > 0:
        return int(requested)
    return max(1, (os.cpu_count() or 1))


def run_inference(data_dir: str | Path,
                  output: str | Path,
                  config: Config,
                  sample_submission: str | Path | None = None,
                  weights_dir: str | Path | None = None,
                  use_model: bool = True,
                  jobs: int | None = None) -> dict:
    """Полный прогон инференса. Возвращает сводку: число чипов, время, путь к файлу."""
    started = time.perf_counter()
    dataset = ChipDataset(data_dir, config)

    template_path = Path(sample_submission) if sample_submission else dataset.sample_submission_path
    if template_path is None or not Path(template_path).exists():
        raise FileNotFoundError(
            "не найден sample_submission.csv — укажите его через --sample-submission")
    template = read_sample_submission(template_path)
    chip_ids = list(dict.fromkeys(template["chip_id"].astype(str)))
    LOGGER.info("чипов в шаблоне: %d, каталог данных: %s", len(chip_ids), data_dir)

    # Меньше двух чипов на процесс — накладные расходы на запуск не окупаются.
    workers = min(resolve_jobs(jobs), max(1, len(chip_ids) // 2))
    rows: list[dict] = []
    failures: list[str] = []

    if workers == 1 or len(chip_ids) <= 1:
        af_model, bs_model = load_models(config, weights_dir, enabled=use_model)
        for chip_id in chip_ids:
            try:
                rows.extend(process_chip(dataset, chip_id, config, af_model, bs_model))
            except Exception as error:
                failures.append(f"{chip_id}: {type(error).__name__}: {error}")
                rows.extend(_empty_rows(dataset, chip_id))
    else:
        import multiprocessing as mp

        # fork дешевле spawn: рабочие процессы наследуют уже импортированные
        # модули, и на коротких прогонах старт интерпретатора не съедает больше
        # времени, чем сама обработка чипов.
        methods = mp.get_all_start_methods()
        context = mp.get_context("fork" if "fork" in methods else "spawn")
        with context.Pool(processes=workers, initializer=_init_worker,
                          initargs=(dataset, config, weights_dir, use_model)) as pool:
            for chip_id, chip_rows, error in pool.imap_unordered(_run_worker, chip_ids, chunksize=1):
                rows.extend(chip_rows)
                if error:
                    failures.append(f"{chip_id}: {error}")

    for failure in failures:
        LOGGER.error("чип обработан пустым ответом — %s", failure)

    frame = write_submission(rows, template, output)
    elapsed = time.perf_counter() - started
    summary = {
        "chips": len(chip_ids),
        "rows": int(len(frame)),
        "failed_chips": len(failures),
        "elapsed_sec": round(elapsed, 2),
        "workers": workers,
        "output": str(output),
    }
    LOGGER.info("готово: %d строк за %.2f с (процессов: %d, ошибок: %d)",
                summary["rows"], elapsed, workers, len(failures))
    return summary


def predicted_pixel_counts(frame: pd.DataFrame) -> dict[int, int]:
    """Сколько пикселей предсказано по каждому классу (для журнала и отчёта)."""
    from .io.rle import rle_pixel_count

    counts: dict[int, int] = {}
    for class_id, rle in frame[["class_id", "rle"]].itertuples(index=False, name=None):
        counts[int(class_id)] = counts.get(int(class_id), 0) + rle_pixel_count(
            "" if pd.isna(rle) else str(rle))
    return counts
