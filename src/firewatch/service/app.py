"""Информационно-аналитический сервис: REST API и веб-карта.

По пространственно-временному запросу (полигон или ограничивающий
прямоугольник плюс интервал дат) сервис возвращает карту термоточек, векторные
контуры пройденной огнём площади и аналитическую справку с площадью в гектарах
и распределением по трём степеням поражения. Тот же результат доступен через
REST без пользовательского интерфейса.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Iterable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..config import load_config
from ..vectorize import SEVERITY_LABELS, feature_collection, summarize
from . import catalog as catalog_module

LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class SpatialTemporalQuery(BaseModel):
    """Пространственно-временной запрос."""

    bbox: list[float] | None = Field(
        default=None, description="[мин. долгота, мин. широта, макс. долгота, макс. широта]")
    geometry: dict | None = Field(default=None, description="полигон в GeoJSON (EPSG:4326)")
    date_from: date | None = None
    date_to: date | None = None
    clip: bool = Field(default=True, description="обрезать контуры по границе запроса")

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        if len(value) != 4:
            raise ValueError("bbox задаётся четырьмя числами")
        min_x, min_y, max_x, max_y = value
        if min_x >= max_x or min_y >= max_y:
            raise ValueError("bbox должен иметь положительные размеры")
        return value


def _query_geometry(request: SpatialTemporalQuery):
    """Геометрия запроса в виде объекта shapely; None — вся территория каталога."""
    from shapely.geometry import box, shape

    if request.geometry:
        try:
            return shape(request.geometry)
        except Exception as error:
            raise HTTPException(status_code=422, detail=f"некорректная геометрия: {error}") from error
    if request.bbox:
        return box(*request.bbox)
    return None


def _in_period(feature: dict, date_from: date | None, date_to: date | None) -> bool:
    if date_from is None and date_to is None:
        return True
    moment = catalog_module.feature_date(feature)
    if moment is None:
        # Дата неизвестна (обезличенный чип) — в интервальный запрос не попадает.
        return False
    if date_from and moment < date_from:
        return False
    if date_to and moment > date_to:
        return False
    return True


def _filter_features(features: Iterable[dict], geometry, request: SpatialTemporalQuery,
                     clip: bool) -> list[dict]:
    from shapely.geometry import mapping, shape

    selected: list[dict] = []
    for feature in features:
        if not _in_period(feature, request.date_from, request.date_to):
            continue
        if geometry is None:
            selected.append(feature)
            continue
        candidate = shape(feature["geometry"])
        if not candidate.intersects(geometry):
            continue
        if not clip or feature["geometry"]["type"] == "Point":
            selected.append(feature)
            continue
        intersection = candidate.intersection(geometry)
        if intersection.is_empty:
            continue
        properties = dict(feature["properties"])
        # Контур обрезан границей запроса — площадь уменьшается в той же
        # пропорции, что и площадь геометрии (участок мал, искажение проекции
        # на отношении площадей практически не сказывается).
        if candidate.area > 0:
            share = intersection.area / candidate.area
            properties["area_ha"] = round(float(properties.get("area_ha", 0.0)) * share, 4)
            properties["pixels"] = int(round(int(properties.get("pixels", 0)) * share))
            properties["clipped"] = share < 0.999
        selected.append({"type": "Feature", "geometry": mapping(intersection), "properties": properties})
    return selected


def _summary_csv(summary: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["severity_class", "severity_label", "area_ha", "pixels", "share_percent"])
    for row in summary["by_severity"]:
        writer.writerow([row["severity_class"], row["severity_label"], row["area_ha"],
                         row["pixels"], row["share_percent"]])
    writer.writerow(["total", "всего", summary["total_area_ha"], "", 100.0 if summary["total_area_ha"] else 0.0])
    return output.getvalue()


def create_app(catalog_dir: str | Path | None = None, config_path: str | Path | None = None) -> FastAPI:
    config = load_config(config_path)
    directory = Path(catalog_dir) if catalog_dir else (
        config.resolve("service.catalog_dir") or Path("data/service_catalog"))
    state = {"catalog": catalog_module.load(directory), "dir": directory}

    app = FastAPI(title="Мониторинг природных пожаров по данным ДЗЗ",
                  description=__doc__, version="1.0.0")

    def current() -> catalog_module.Catalog:
        return state["catalog"]

    def run_query(request: SpatialTemporalQuery) -> dict:
        data = current()
        geometry = _query_geometry(request)
        limit = int(config.get_path("service.max_features", 20000))
        points = _filter_features(data.hotspots, geometry, request, clip=False)[:limit]
        scars = _filter_features(data.burn_scars, geometry, request, clip=request.clip)[:limit]
        return {
            "query": json.loads(request.model_dump_json()),
            "hotspots": feature_collection(points),
            "burn_scars": feature_collection(scars),
            "summary": {**summarize(scars), "hotspots": len(points)},
        }

    @app.get("/health")
    def health() -> dict:
        data = current()
        return {"status": "ok", "catalog_dir": str(state["dir"]),
                "hotspots": len(data.hotspots), "burn_scars": len(data.burn_scars),
                "empty": data.is_empty}

    @app.get("/api/catalog")
    def catalog_info() -> dict:
        data = current()
        return {"catalog_dir": str(state["dir"]), "scenes": data.scenes,
                "hotspots_total": len(data.hotspots), "contours_total": len(data.burn_scars),
                "severity_labels": SEVERITY_LABELS}

    @app.post("/api/catalog/reload")
    def catalog_reload() -> dict:
        state["catalog"] = catalog_module.load(state["dir"])
        return {"status": "reloaded", "hotspots": len(state["catalog"].hotspots),
                "burn_scars": len(state["catalog"].burn_scars)}

    @app.post("/api/query")
    def query(request: SpatialTemporalQuery) -> dict:
        return run_query(request)

    def parse_bbox(bbox: str | None) -> list[float] | None:
        if not bbox:
            return None
        try:
            values = [float(part) for part in bbox.split(",")]
        except ValueError as error:
            raise HTTPException(status_code=422, detail="bbox задаётся числами через запятую") from error
        if len(values) != 4:
            raise HTTPException(status_code=422, detail="bbox задаётся четырьмя числами")
        return values

    @app.get("/api/hotspots")
    def get_hotspots(bbox: str | None = None, date_from: date | None = None,
                     date_to: date | None = None) -> dict:
        request = SpatialTemporalQuery(bbox=parse_bbox(bbox), date_from=date_from, date_to=date_to)
        return run_query(request)["hotspots"]

    @app.get("/api/burn-scars")
    def get_burn_scars(bbox: str | None = None, date_from: date | None = None,
                       date_to: date | None = None, clip: bool = True,
                       download: bool = False) -> Response:
        request = SpatialTemporalQuery(bbox=parse_bbox(bbox), date_from=date_from,
                                       date_to=date_to, clip=clip)
        collection = run_query(request)["burn_scars"]
        if not download:
            return JSONResponse(collection)
        payload = json.dumps(collection, ensure_ascii=False, indent=2)
        return Response(content=payload, media_type="application/geo+json",
                        headers={"Content-Disposition": 'attachment; filename="burn_scars.geojson"'})

    @app.get("/api/summary")
    def get_summary(bbox: str | None = None, date_from: date | None = None,
                    date_to: date | None = None, clip: bool = True,
                    format: Literal["json", "csv"] = Query(default="json")) -> Response:
        request = SpatialTemporalQuery(bbox=parse_bbox(bbox), date_from=date_from,
                                       date_to=date_to, clip=clip)
        result = run_query(request)
        summary = result["summary"]
        if format == "csv":
            return Response(content=_summary_csv(summary), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="fire_summary.csv"'})
        return JSONResponse(summary)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app(os.environ.get("FIREWATCH_CATALOG_DIR"), os.environ.get("FIREWATCH_CONFIG"))
