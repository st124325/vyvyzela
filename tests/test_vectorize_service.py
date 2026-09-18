"""Векторизация результатов и информационно-аналитический сервис."""

from __future__ import annotations

import json

import numpy as np
import pytest

from firewatch.modules import active_fire, burn_severity
from firewatch.service import catalog as catalog_module
from firewatch.vectorize import hotspots, polygonize, summarize

pytest.importorskip("shapely")


@pytest.fixture(scope="module")
def service_catalog(tmp_path_factory, synthetic_dir, config):
    target = tmp_path_factory.mktemp("catalog")
    catalog_module.build(synthetic_dir, target, config)
    return target


def test_polygon_area_matches_pixel_count(dataset, config):
    chip = dataset.load(dataset.chip_ids("bs")[0])
    result = burn_severity.segment(chip, config)
    features = polygonize(result.severity, chip)
    if not features:
        pytest.skip("на этом чипе контуров нет")
    pixel_area_ha = chip.gsd ** 2 / 10000.0
    for feature in features:
        expected = feature["properties"]["pixels"] * pixel_area_ha
        assert feature["properties"]["area_ha"] == pytest.approx(expected, rel=1e-6)


def test_summary_shares_sum_to_hundred(dataset, config):
    chip = dataset.load(dataset.chip_ids("bs")[0])
    features = polygonize(burn_severity.segment(chip, config).severity, chip)
    summary = summarize(features)
    if summary["total_area_ha"] == 0:
        pytest.skip("гарей не найдено")
    assert sum(row["share_percent"] for row in summary["by_severity"]) == pytest.approx(100.0, abs=0.05)


def test_hotspots_are_points_in_wgs84(dataset, config):
    chip = dataset.load(dataset.chip_ids("af")[0])
    result = active_fire.detect(chip, config)
    for feature in hotspots(result.mask, chip):
        longitude, latitude = feature["geometry"]["coordinates"]
        assert feature["geometry"]["type"] == "Point"
        assert -180 <= longitude <= 180 and -90 <= latitude <= 90


def test_catalog_contains_both_layers(service_catalog):
    catalog = catalog_module.load(service_catalog)
    assert catalog.scenes
    assert not catalog.is_empty


def _client(catalog_dir):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from firewatch.service.app import create_app

    return TestClient(create_app(catalog_dir))


def test_health_and_catalog_endpoints(service_catalog):
    client = _client(service_catalog)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/catalog").json()["scenes"]


def test_query_returns_geojson_and_summary(service_catalog):
    client = _client(service_catalog)
    catalog = catalog_module.load(service_catalog)
    bounds = [scene["bbox"] for scene in catalog.scenes if scene.get("bbox")]
    bbox = [min(b[0] for b in bounds) - 0.1, min(b[1] for b in bounds) - 0.1,
            max(b[2] for b in bounds) + 0.1, max(b[3] for b in bounds) + 0.1]

    response = client.post("/api/query", json={"bbox": bbox})
    assert response.status_code == 200
    payload = response.json()
    assert payload["hotspots"]["type"] == "FeatureCollection"
    assert payload["burn_scars"]["type"] == "FeatureCollection"
    assert payload["summary"]["total_area_ha"] >= 0
    assert len(payload["summary"]["by_severity"]) == 3


def test_query_outside_area_is_empty(service_catalog):
    client = _client(service_catalog)
    payload = client.post("/api/query", json={"bbox": [0.0, 0.0, 1.0, 1.0]}).json()
    assert payload["burn_scars"]["features"] == []
    assert payload["summary"]["total_area_ha"] == 0


def test_period_filter_excludes_other_dates(service_catalog):
    client = _client(service_catalog)
    payload = client.post("/api/query", json={"date_from": "1990-01-01", "date_to": "1990-12-31"}).json()
    assert payload["hotspots"]["features"] == []


def test_exports_are_machine_readable(service_catalog):
    client = _client(service_catalog)
    geojson = client.get("/api/burn-scars?download=true")
    assert geojson.status_code == 200
    assert "attachment" in geojson.headers["content-disposition"]
    parsed = json.loads(geojson.content)
    assert parsed["type"] == "FeatureCollection"
    for feature in parsed["features"]:
        # Атрибуты выгрузки: идентификатор контура, класс степени и площадь.
        assert {"contour_id", "severity_class", "area_ha"} <= set(feature["properties"])

    csv_response = client.get("/api/summary?format=csv")
    assert csv_response.status_code == 200
    assert csv_response.text.splitlines()[0].startswith("severity_class")


def test_invalid_bbox_is_rejected(service_catalog):
    client = _client(service_catalog)
    assert client.post("/api/query", json={"bbox": [10.0, 10.0, 5.0, 5.0]}).status_code == 422
    assert client.get("/api/hotspots?bbox=1,2,3").status_code == 422
