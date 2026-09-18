"""Подготовка и чтение каталога информационно-аналитического сервиса.

Сервис работает на заранее подготовленном ограниченном наборе сцен: чипы
прогоняются обоими модулями один раз, результат векторизуется и складывается в
каталог (два GeoJSON и описание сцен). Запрос пользователя после этого
отрабатывает за доли секунды и не требует доступа к растрам.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..config import Config
from ..io.chips import ChipDataset
from ..models import load_models
from ..modules import active_fire, burn_severity
from ..vectorize import feature_collection, hotspots, polygonize

LOGGER = logging.getLogger(__name__)

HOTSPOTS_FILE = "hotspots.geojson"
BURN_SCARS_FILE = "burn_scars.geojson"
CATALOG_FILE = "catalog.json"


@dataclass
class Catalog:
    """Загруженный каталог: точки, контуры и описание сцен."""

    hotspots: list[dict]
    burn_scars: list[dict]
    scenes: list[dict]
    source: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.hotspots and not self.burn_scars


def parse_date(value) -> date | None:
    """Дата из строки meta.csv: поддерживаются 'ГГГГ-ММ-ДД' и полный ISO-момент."""
    if value in (None, "", "nan"):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        LOGGER.debug("не удалось разобрать дату %r", value)
        return None


def feature_date(feature: dict) -> date | None:
    properties = feature.get("properties", {})
    for key in ("acq_datetime", "date_post", "date"):
        parsed = parse_date(properties.get(key))
        if parsed is not None:
            return parsed
    return None


def build(data_dir: str | Path, output_dir: str | Path, config: Config,
          weights_dir: str | Path | None = None, limit: int | None = None) -> dict:
    """Прогоняет чипы через оба модуля и сохраняет каталог сервиса."""
    dataset = ChipDataset(data_dir, config)
    af_model, bs_model = load_models(config, weights_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    min_contour_px = int(config.get_path("service.min_contour_px", 1))

    all_hotspots: list[dict] = []
    all_scars: list[dict] = []
    scenes: list[dict] = []
    skipped_without_geo = 0

    for chip_id in dataset.chip_ids()[:limit]:
        chip = dataset.load(chip_id)
        if not chip.geo.is_known:
            # Тестовые чипы обезличены: без геопривязки их нельзя положить на карту.
            skipped_without_geo += 1
            continue
        if chip.kind == "af":
            result = active_fire.detect(chip, config, model=af_model)
            features = hotspots(result.mask, chip)
            all_hotspots.extend(features)
            scene_date = parse_date(chip.meta.get("acq_datetime"))
            scenes.append({"chip_id": chip_id, "kind": "af", "date": scene_date.isoformat() if scene_date else None,
                           "hotspots": len(features), "bbox": _bbox(features)})
        else:
            result = burn_severity.segment(chip, config, model=bs_model)
            features = polygonize(result.severity, chip, min_pixels=min_contour_px)
            all_scars.extend(features)
            scene_date = parse_date(chip.meta.get("date_post"))
            scenes.append({"chip_id": chip_id, "kind": "bs", "date": scene_date.isoformat() if scene_date else None,
                           "contours": len(features), "bbox": _bbox(features)})

    (output_dir / HOTSPOTS_FILE).write_text(
        json.dumps(feature_collection(all_hotspots), ensure_ascii=False), encoding="utf-8")
    (output_dir / BURN_SCARS_FILE).write_text(
        json.dumps(feature_collection(all_scars), ensure_ascii=False), encoding="utf-8")
    summary = {
        "source_dir": str(Path(data_dir).resolve()),
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scenes": scenes,
        "hotspots_total": len(all_hotspots),
        "contours_total": len(all_scars),
        "skipped_without_georeference": skipped_without_geo,
    }
    (output_dir / CATALOG_FILE).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("каталог собран: %d термоточек, %d контуров, пропущено без геопривязки %d",
                len(all_hotspots), len(all_scars), skipped_without_geo)
    return summary


def _bbox(features: list[dict]) -> list[float] | None:
    """Ограничивающий прямоугольник набора объектов в градусах."""
    if not features:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(coordinates) -> None:
        if isinstance(coordinates[0], (int, float)):
            xs.append(float(coordinates[0]))
            ys.append(float(coordinates[1]))
            return
        for item in coordinates:
            walk(item)

    for feature in features:
        walk(feature["geometry"]["coordinates"])
    return [min(xs), min(ys), max(xs), max(ys)]


def load(directory: str | Path) -> Catalog:
    """Читает каталог с диска. Отсутствие файлов — пустой каталог, не ошибка."""
    directory = Path(directory)
    hotspots_path = directory / HOTSPOTS_FILE
    scars_path = directory / BURN_SCARS_FILE
    catalog_path = directory / CATALOG_FILE

    def read_features(path: Path) -> list[dict]:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("features", []))

    scenes: list[dict] = []
    if catalog_path.exists():
        scenes = json.loads(catalog_path.read_text(encoding="utf-8")).get("scenes", [])
    return Catalog(hotspots=read_features(hotspots_path), burn_scars=read_features(scars_path),
                   scenes=scenes, source=str(directory))
