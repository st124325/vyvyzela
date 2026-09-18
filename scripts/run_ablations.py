#!/usr/bin/env python3
"""Абляции: вклад отдельных механизмов в метрику.

    python scripts/run_ablations.py --data-dir data/train --truth data/train/truth.csv

Каждый вариант — это полный прогон инференса с точечным переопределением
конфигурации и подсчёт метрики по одному и тому же эталону. Результат печатается
таблицей Markdown, пригодной для вставки в отчёт.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firewatch.config import load_config  # noqa: E402
from firewatch.io.chips import ChipDataset  # noqa: E402
from firewatch.metrics import evaluate_files  # noqa: E402
from firewatch.pipeline import run_inference  # noqa: E402
from firewatch.utils.logging import setup_logging  # noqa: E402

# Каждая строка: название варианта и переопределения относительно полной схемы.
ABLATIONS: list[tuple[str, list[str]]] = [
    ("полная схема", []),
    ("AF: без фильтра техногенных аномалий", ["active_fire.reject.technogenic_landcover=[]"]),
    ("AF: без контекстного теста (только абсолютные пороги)", ["active_fire.context.i4_mad_k=1000"]),
    ("AF: без отбраковки блика и облаков",
     ["active_fire.reject.glint_i3=10", "active_fire.reject.cloud_i1=10",
      "active_fire.reject.bg_dt_std=1000"]),
    ("BS: единая шкала dNBR (пороги USGS для всех типов покрова)",
     ["burn_severity.thresholds.grass={t1: 0.100, t2: 0.270, t3: 0.660}",
      "burn_severity.thresholds.crop={t1: 0.100, t2: 0.270, t3: 0.660}",
      "burn_severity.thresholds.wetland={t1: 0.100, t2: 0.270, t3: 0.660}",
      "burn_severity.thresholds.other={t1: 0.100, t2: 0.270, t3: 0.660}",
      "burn_severity.thresholds.default={t1: 0.100, t2: 0.270, t3: 0.660}"]),
    ("BS: без подтверждения по dNDVI и без строгости на пашне",
     ["burn_severity.confirm.min_dndvi=-1", "burn_severity.confirm.crop_min_dndvi=-1",
      "burn_severity.confirm.crop_extra_dnbr=0"]),
    ("BS: без радиолокации", ["burn_severity.sar.enabled=false"]),
    ("BS: без морфологической постобработки",
     ["burn_severity.morphology.min_object_px=1", "burn_severity.morphology.fill_holes_px=0",
      "burn_severity.morphology.smooth_iterations=0"]),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Абляции по компонентам решения")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--weights-dir", default=None)
    parser.add_argument("--no-model", action="store_true")
    parser.add_argument("--work-dir", default="runs/ablations")
    parser.add_argument("--jobs", type=int, default=0)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args(argv)

    setup_logging()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    base_config = load_config(args.config)
    dataset = ChipDataset(args.data_dir, base_config)
    chip_ids = dataset.chip_ids()
    shapes = {cid: dataset.shape_of(cid) for cid in chip_ids}
    shapes = {cid: shape for cid, shape in shapes.items() if shape is not None}
    kinds = {cid: dataset.kind_of(cid) for cid in chip_ids}

    results = []
    for index, (name, overrides) in enumerate(ABLATIONS):
        config = load_config(args.config, overrides)
        output = work_dir / f"submission_{index:02d}.csv"
        summary = run_inference(args.data_dir, output, config, weights_dir=args.weights_dir,
                                use_model=not args.no_model, jobs=args.jobs)
        report = evaluate_files(output, args.truth, shapes, kinds).as_dict()
        results.append({"name": name, "overrides": overrides, "elapsed_sec": summary["elapsed_sec"],
                        **{k: report[k] for k in ("score", "f1_af", "iou_burn", "miou_sev")}})

    header = "| вариант | Score | F1_af | IoU_burn | mIoU_sev | время, с |"
    print("\n" + header)
    print("|---|---|---|---|---|---|")
    for row in results:
        print(f"| {row['name']} | {row['score']:.4f} | {row['f1_af']:.4f} | "
              f"{row['iou_burn']:.4f} | {row['miou_sev']:.4f} | {row['elapsed_sec']:.1f} |")

    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
