"""Векторизация растровых результатов: контуры гарей и точки термоточек.

Геометрия выдаётся в EPSG:4326 (этого ждёт картографическая подложка), а
площадь считается в исходной проекции чипа по числу пикселей и размеру пикселя
на местности — так на площадь не влияет искажение при перепроецировании.
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

from .io.chips import Chip, GeoReference

LOGGER = logging.getLogger(__name__)

WGS84 = "EPSG:4326"
SEVERITY_LABELS = {1: "слабая", 2: "средняя", 3: "сильная"}


def pixel_area_m2(geo: GeoReference, fallback_gsd: float) -> float:
    """Площадь пикселя в квадратных метрах."""
    if geo.transform is None:
        return fallback_gsd ** 2
    scale_x, _, _, _, scale_y, _ = geo.transform
    area = abs(float(scale_x) * float(scale_y))
    # Географическая система координат: масштаб задан в градусах, площадь из
    # него не получить — берём размер пикселя на местности из meta.csv.
    if area < 1e-3:
        return fallback_gsd ** 2
    return area


def _to_wgs84(geometry: dict, crs: str | None) -> dict:
    if not crs or crs.upper().replace(" ", "") in (WGS84, "EPSG:4326"):
        return geometry
    from rasterio.warp import transform_geom

    return transform_geom(crs, WGS84, geometry, precision=6)


def polygonize(severity: np.ndarray, chip: Chip, min_pixels: int = 1) -> list[dict]:
    """Контуры гари по классам степени поражения в виде GeoJSON-объектов."""
    if not chip.geo.is_known:
        LOGGER.debug("чип %s без геопривязки — векторизация пропущена", chip.chip_id)
        return []
    from affine import Affine
    from rasterio import features as rio_features

    transform = Affine(*chip.geo.transform)
    pixel_area = pixel_area_m2(chip.geo, chip.gsd)
    area_ha = pixel_area / 10000.0
    results: list[dict] = []
    for class_id in (1, 2, 3):
        mask = severity == class_id
        if not mask.any():
            continue
        for index, (geometry, _value) in enumerate(
                rio_features.shapes(mask.astype(np.uint8), mask=mask, transform=transform)):
            pixels = _polygon_pixel_count(geometry, pixel_area)
            if pixels < min_pixels:
                continue
            results.append({
                "type": "Feature",
                "geometry": _to_wgs84(geometry, chip.geo.crs),
                "properties": {
                    "contour_id": f"{chip.chip_id}_s{class_id}_{index:04d}",
                    "chip_id": chip.chip_id,
                    "severity_class": class_id,
                    "severity_label": SEVERITY_LABELS[class_id],
                    "pixels": int(pixels),
                    "area_ha": round(pixels * area_ha, 4),
                    "date_pre": _meta_value(chip, "date_pre"),
                    "date_post": _meta_value(chip, "date_post"),
                },
            })
    return results


def _polygon_pixel_count(geometry: dict, pixel_area: float) -> int:
    """Число пикселей внутри полигона.

    Границы полигона проходят строго по границам пикселей (так устроен обход
    растра), поэтому площадь в проекции чипа, делённая на площадь пикселя, даёт
    точное целое число — растеризовать контур повторно не нужно. Дыры внутри
    контура shapely вычитает из площади сам.
    """
    from shapely.geometry import shape

    return int(round(shape(geometry).area / pixel_area))


def hotspots(mask: np.ndarray, chip: Chip) -> list[dict]:
    """Термоточки: центры пикселей активного горения в виде точечных объектов."""
    if not chip.geo.is_known or not mask.any():
        return []
    from affine import Affine

    transform = Affine(*chip.geo.transform)
    area_ha = pixel_area_m2(chip.geo, chip.gsd) / 10000.0
    rows, cols = np.nonzero(mask)
    features: list[dict] = []
    for index, (row, col) in enumerate(zip(rows, cols)):
        x, y = transform * (float(col) + 0.5, float(row) + 0.5)
        geometry = _to_wgs84({"type": "Point", "coordinates": [x, y]}, chip.geo.crs)
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "hotspot_id": f"{chip.chip_id}_{index:05d}",
                "chip_id": chip.chip_id,
                "acq_datetime": _meta_value(chip, "acq_datetime"),
                "satellite": _meta_value(chip, "satellite"),
                "pixel_area_ha": round(area_ha, 3),
            },
        })
    return features


def _meta_value(chip: Chip, key: str):
    import pandas as pd

    value = chip.meta.get(key)
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return str(value)


def feature_collection(features: Iterable[dict]) -> dict:
    collection = list(features)
    return {"type": "FeatureCollection", "crs": {"type": "name",
                                                 "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": collection}


def summarize(features: Iterable[dict]) -> dict:
    """Аналитическая справка: площадь гари и её распределение по степеням."""
    totals = {1: 0.0, 2: 0.0, 3: 0.0}
    pixels = {1: 0, 2: 0, 3: 0}
    contours = 0
    for feature in features:
        properties = feature["properties"]
        class_id = int(properties["severity_class"])
        totals[class_id] += float(properties["area_ha"])
        pixels[class_id] += int(properties["pixels"])
        contours += 1
    total_area = sum(totals.values())
    return {
        "total_area_ha": round(total_area, 3),
        "contours": contours,
        "by_severity": [
            {
                "severity_class": class_id,
                "severity_label": SEVERITY_LABELS[class_id],
                "area_ha": round(totals[class_id], 3),
                "pixels": pixels[class_id],
                "share_percent": round(100.0 * totals[class_id] / total_area, 2) if total_area else 0.0,
            }
            for class_id in (1, 2, 3)
        ],
    }
