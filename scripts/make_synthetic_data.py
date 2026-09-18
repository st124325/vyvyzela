#!/usr/bin/env python3
"""Генератор синтетического набора в структуре данных кейса.

Нужен для двух целей: прогон всего конвейера (обучение → инференс → метрика →
сервис) без доступа к выданному архиву и автотесты. Физика упрощена, но
воспроизведены свойства, на которые опирается решение: субпиксельные очаги
в канале I4, техногенные термоаномалии на застройке, солнечный блик, различие
шкалы dNBR между лесом и степью, облачность в SCL, ложные изменения на пашне
и спекл-шум радиолокации.

Синтетика НЕ является данными соревнования и не используется для обучения
финальных весов — только для проверки работоспособности кода.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

# Коды ESA WorldCover v200
LC_TREE, LC_SHRUB, LC_GRASS, LC_CROP, LC_BUILT, LC_BARE, LC_WATER, LC_WETLAND = 10, 20, 30, 40, 50, 60, 80, 90
AF_SIZE, BS_SIZE = 256, 512
AF_GSD, BS_GSD = 375.0, 20.0
# Юг России: примерная зона UTM 37N, чтобы геопривязка была правдоподобной
CRS = "EPSG:32637"


def _write(path: Path, array: np.ndarray, gsd: float, origin: tuple[float, float],
           dtype: str, georeferenced: bool = True) -> None:
    array = np.atleast_3d(array.transpose(1, 2, 0) if array.ndim == 3 else array[..., None])
    array = array.transpose(2, 0, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff", "height": array.shape[1], "width": array.shape[2],
        "count": array.shape[0], "dtype": dtype, "compress": "deflate",
    }
    if georeferenced:
        profile["crs"] = CRS
        profile["transform"] = from_origin(origin[0], origin[1], gsd, gsd)
    with rasterio.open(path, "w", **profile) as handle:
        handle.write(array.astype(dtype))


def _landcover_map(rng: np.random.Generator, size: int, classes: list[int]) -> np.ndarray:
    """Крупные однородные пятна типов покрова (сглаженный шум по классам)."""
    from scipy import ndimage

    field = ndimage.gaussian_filter(rng.normal(size=(size, size)), sigma=size / 12.0)
    quantiles = np.quantile(field, np.linspace(0, 1, len(classes) + 1)[1:-1])
    index = np.digitize(field, quantiles)
    return np.asarray(classes, dtype=np.int32)[index]


def make_af_chip(rng: np.random.Generator, with_fire: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Возвращает (каналы I1–I5, вспомогательные слои, эталонную маску)."""
    from scipy import ndimage

    size = AF_SIZE
    landcover = _landcover_map(rng, size, [LC_GRASS, LC_CROP, LC_TREE, LC_BUILT, LC_WATER, LC_BARE])
    night = rng.random() < 0.4
    solar_zenith = np.full((size, size), 110.0 if night else float(rng.uniform(25, 60)), np.float32)

    # Фон: I5 около 300 K, I4 чуть холоднее; днём нагрев открытого грунта заметнее.
    base = 288.0 if night else 300.0
    i5 = base + ndimage.gaussian_filter(rng.normal(0, 3.0, (size, size)), 6).astype(np.float32)
    i5 += np.where(landcover == LC_BARE, 6.0 if not night else 1.0, 0.0)
    i5 -= np.where(landcover == LC_WATER, 4.0, 0.0)
    i4 = i5 - 2.0 + rng.normal(0, 0.6, (size, size)).astype(np.float32)
    i4 += np.where(landcover == LC_BARE, 4.0 if not night else 0.5, 0.0)

    reflect = 0.0 if night else 1.0
    i1 = (0.08 + 0.05 * rng.random((size, size))) * reflect
    i2 = (0.25 + 0.10 * rng.random((size, size))) * reflect
    i3 = (0.18 + 0.06 * rng.random((size, size))) * reflect
    i1 = np.where(landcover == LC_WATER, 0.03 * reflect, i1)
    i2 = np.where(landcover == LC_WATER, 0.02 * reflect, i2)

    truth = np.zeros((size, size), np.uint8)

    def add_hotspots(count: int, amplitude: tuple[float, float], mark: bool,
                     allowed: np.ndarray | None = None) -> None:
        for _ in range(count):
            for _attempt in range(50):
                row, col = rng.integers(8, size - 8, size=2)
                if allowed is None or allowed[row, col]:
                    break
            else:
                continue
            gain = float(rng.uniform(*amplitude))
            cluster = [(0, 0)] + ([(0, 1), (1, 0)] if rng.random() < 0.45 else [])
            for delta_row, delta_col in cluster:
                r, c = row + delta_row, col + delta_col
                i4[r, c] = min(i4[r, c] + gain, 367.0)      # канал I4 насыщается
                i5[r, c] += gain * 0.22                     # в тепловом канале вклад меньше
                if mark:
                    truth[r, c] = 1

    natural = np.isin(landcover, [LC_GRASS, LC_CROP, LC_TREE])
    if with_fire:
        add_hotspots(int(rng.integers(2, 7)), (25.0, 90.0), mark=True, allowed=natural)
    # Техногенные термоаномалии: всегда на застройке, в эталоне это не пожар.
    add_hotspots(int(rng.integers(1, 4)), (30.0, 80.0), mark=False, allowed=landcover == LC_BUILT)
    if not night:
        # Солнечный блик: высокое SWIR-отражение и умеренный нагрев.
        glint = (landcover == LC_WATER) & (rng.random((size, size)) < 0.01)
        i3 = np.where(glint, 0.45, i3)
        i4 = np.where(glint, i4 + 14.0, i4)

    aux = np.stack([
        landcover.astype(np.float32),
        (120 + 60 * rng.random((size, size))).astype(np.float32),     # рельеф, м
        solar_zenith,
        np.full((size, size), float(rng.uniform(5, 45)), np.float32),  # зенит сенсора
        np.full((size, size), float(rng.uniform(285, 310)), np.float32),  # T2m, K
        np.full((size, size), float(rng.uniform(15, 60)), np.float32),    # влажность, %
        np.full((size, size), float(rng.uniform(1, 9)), np.float32),      # ветер, м/с
        np.ones((size, size), np.float32),                                # маска валидности
    ])
    bands = np.stack([i1, i2, i3, i4, i5]).astype(np.float32)
    return bands, aux, truth


def make_bs_chip(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Возвращает (S2 до, S2 после, S1 до, S1 после, вспомогательные слои, эталон)."""
    from scipy import ndimage

    size = BS_SIZE
    landcover = _landcover_map(rng, size, [LC_GRASS, LC_CROP, LC_TREE, LC_SHRUB, LC_WETLAND, LC_BARE])
    rows, cols = np.mgrid[0:size, 0:size]

    # Отражение «до»: живая растительность — высокий NIR, низкий SWIR.
    nir = 0.30 + 0.12 * (np.isin(landcover, [LC_TREE, LC_SHRUB])) + 0.03 * rng.random((size, size))
    swir1 = 0.16 + 0.04 * rng.random((size, size))
    swir2 = 0.09 + 0.03 * rng.random((size, size))
    red = 0.05 + 0.02 * rng.random((size, size))

    severity = np.zeros((size, size), np.uint8)
    for _ in range(int(rng.integers(1, 4))):
        center_row, center_col = rng.integers(size * 0.2, size * 0.8, size=2)
        radius_row, radius_col = rng.integers(size * 0.06, size * 0.22, size=2)
        angle = rng.uniform(0, np.pi)
        y = (rows - center_row) * np.cos(angle) + (cols - center_col) * np.sin(angle)
        x = -(rows - center_row) * np.sin(angle) + (cols - center_col) * np.cos(angle)
        distance = (y / radius_row) ** 2 + (x / radius_col) ** 2
        distance = distance + 0.25 * ndimage.gaussian_filter(rng.normal(size=(size, size)), 8)
        inside = distance < 1.0
        # Ядро выгорает сильнее края — отсюда три степени поражения.
        severity[inside & (distance < 0.35)] = 3
        severity[inside & (distance >= 0.35) & (distance < 0.7)] = 2
        severity[inside & (distance >= 0.7)] = 1

    # Одинаковое воздействие даёт разный dNBR: в степи биомассы вдесятеро меньше.
    biomass = np.where(np.isin(landcover, [LC_TREE, LC_SHRUB]), 1.0,
                       np.where(landcover == LC_WETLAND, 0.9, 0.45)).astype(np.float32)
    drop = np.choose(severity, [np.zeros_like(biomass), 0.18 * biomass, 0.40 * biomass, 0.75 * biomass])

    nir_post = np.clip(nir - drop * 0.55, 0.01, None)
    swir2_post = np.clip(swir2 + drop * 0.30, 0.01, None)
    swir1_post = np.clip(swir1 + drop * 0.18, 0.01, None)
    red_post = np.clip(red + drop * 0.05, 0.01, None)

    # Фенология без пожара: убранное поле на пашне — классический ложный контур.
    harvested = (landcover == LC_CROP) & (
        ndimage.gaussian_filter(rng.normal(size=(size, size)), 14) > 0.9)
    nir_post = np.where(harvested, nir_post - 0.10, nir_post)
    swir1_post = np.where(harvested, swir1_post + 0.06, swir1_post)
    swir2_post = np.where(harvested, swir2_post + 0.05, swir2_post)
    red_post = np.where(harvested, red_post + 0.06, red_post)

    scl_pre = np.full((size, size), 4, np.float32)
    scl_post = np.full((size, size), 4, np.float32)
    scl_pre[landcover == LC_WATER] = 6
    scl_post[landcover == LC_WATER] = 6
    # Облачность задаётся долей площади, чтобы каждый чип гарантированно
    # содержал закрытые пиксели: медиана выданного набора 1,6 %, максимум 48,5 %.
    cloud_field = ndimage.gaussian_filter(rng.normal(size=(size, size)), 20)
    cloud_fraction = float(rng.uniform(0.02, 0.25))
    clouds = cloud_field > np.quantile(cloud_field, 1.0 - cloud_fraction)
    scl_post[clouds] = 9  # облако высокой вероятности
    nir_post = np.where(clouds, 0.55, nir_post)
    swir2_post = np.where(clouds, 0.45, swir2_post)

    def stack_optical(b4, b8a, b11, b12, scl):
        green = b4 + 0.02
        blue = b4 * 0.8
        edge = b8a * np.array([0.5, 0.8, 0.95])[:, None, None]
        return np.concatenate([
            np.stack([blue, green, b4]), edge, np.stack([b8a, b11, b12, scl])]).astype(np.float32)

    pre = stack_optical(red, nir, swir1, swir2, scl_pre)
    post = stack_optical(red_post, nir_post, swir1_post, swir2_post, scl_post)

    # Радиолокация: гарь снижает VH, поверх лежит спекл-шум.
    speckle = lambda: rng.normal(0, 0.8, (size, size)).astype(np.float32)
    vh_pre = -17.0 + 2.0 * biomass + speckle()
    vv_pre = -9.0 + 1.5 * biomass + speckle()
    vh_post = vh_pre - np.choose(severity, [0.0, 0.8, 1.8, 3.0]) + speckle() * 0.5
    vv_post = vv_pre - np.choose(severity, [0.0, 0.4, 0.9, 1.5]) + speckle() * 0.5

    slope = np.abs(ndimage.gaussian_filter(rng.normal(size=(size, size)), 10)) * 12
    aux = np.stack([
        (150 + 80 * ndimage.gaussian_filter(rng.normal(size=(size, size)), 12)).astype(np.float32),
        slope.astype(np.float32),
        (180 * rng.random((size, size))).astype(np.float32),
        landcover.astype(np.float32),
    ])
    s1_pre = np.stack([vv_pre, vh_pre]).astype(np.float32)
    s1_post = np.stack([vv_post, vh_post]).astype(np.float32)
    return pre, post, s1_pre, s1_post, aux, severity


def generate(output: Path, n_af: int, n_bs: int, seed: int, split: str, georeferenced: bool) -> None:
    rng = np.random.default_rng(seed)
    output.mkdir(parents=True, exist_ok=True)
    meta_rows, sample_rows, truth_rows = [], [], []
    scale_s2, scale_s1 = 10000.0, 100.0

    for index in range(n_af):
        chip_id = f"AF_{split}_{index:06d}"
        with_fire = rng.random() < 0.7
        bands, aux, truth = make_af_chip(rng, with_fire)
        origin = (300000.0 + index * 1000, 5200000.0 - index * 1000)
        _write(output / "af" / f"{chip_id}.tif", bands, AF_GSD, origin, "float32", georeferenced)
        _write(output / "af" / f"{chip_id}_aux.tif", aux, AF_GSD, origin, "float32", georeferenced)
        if split == "tr":
            _write(output / "af" / f"{chip_id}_mask.tif", truth[None], AF_GSD, origin, "uint8", georeferenced)
        meta_rows.append({
            "chip_id": chip_id, "kind": "af", "fire_event_id": f"EV_{index // 2:04d}",
            "width": AF_SIZE, "height": AF_SIZE, "gsd": AF_GSD,
            "acq_datetime": f"2024-0{4 + index % 6}-{5 + index % 20:02d}T09:35:00Z",
            "satellite": ["Suomi NPP", "NOAA-20", "NOAA-21"][index % 3],
            "valid_frac": 1.0, "cloud_frac": float("nan"), "n_fire_px": int(truth.sum()),
        })
        sample_rows.append({"chip_id": chip_id, "class_id": 1, "rle": ""})
        truth_rows.append((chip_id, 1, truth == 1))

    for index in range(n_bs):
        chip_id = f"BS_{split}_{index:06d}"
        pre, post, s1_pre, s1_post, aux, severity = make_bs_chip(rng)
        origin = (400000.0 + index * 2000, 5100000.0 - index * 2000)
        base = output / "bs"
        # Масштабируется только отражение: слой SCL хранит коды классов как есть.
        def scaled(stack: np.ndarray) -> np.ndarray:
            output = stack.copy()
            output[:9] = np.clip(output[:9] * scale_s2, 0, 65535)
            return output

        _write(base / f"{chip_id}_pre.tif", scaled(pre), BS_GSD, origin, "uint16", georeferenced)
        _write(base / f"{chip_id}_post.tif", scaled(post), BS_GSD, origin, "uint16", georeferenced)
        _write(base / f"{chip_id}_s1_pre.tif", (s1_pre * scale_s1), BS_GSD, origin, "int16", georeferenced)
        _write(base / f"{chip_id}_s1_post.tif", (s1_post * scale_s1), BS_GSD, origin, "int16", georeferenced)
        _write(base / f"{chip_id}_aux.tif", aux, BS_GSD, origin, "float32", georeferenced)
        if split == "tr":
            _write(base / f"{chip_id}_mask.tif", severity[None], BS_GSD, origin, "uint8", georeferenced)
        pixel_area_ha = (BS_GSD ** 2) / 10000.0
        meta_rows.append({
            "chip_id": chip_id, "kind": "bs", "fire_event_id": f"EV_{100 + index:04d}",
            "width": BS_SIZE, "height": BS_SIZE, "gsd": BS_GSD,
            "date_pre": f"2024-0{4 + index % 5}-{1 + index % 20:02d}",
            "date_post": f"2024-0{5 + index % 5}-{2 + index % 20:02d}",
            "s1_date_pre": f"2024-0{4 + index % 5}-{2 + index % 20:02d}",
            "s1_date_post": f"2024-0{5 + index % 5}-{3 + index % 20:02d}",
            "valid_frac": 1.0, "cloud_frac": round(float(np.mean(post[9] == 9)), 4),
            "burn_area_ha": round(float((severity > 0).sum() * pixel_area_ha), 2),
            "sev1_px": int((severity == 1).sum()), "sev2_px": int((severity == 2).sum()),
            "sev3_px": int((severity == 3).sum()),
        })
        for cls in (1, 2, 3):
            sample_rows.append({"chip_id": chip_id, "class_id": cls, "rle": ""})
            truth_rows.append((chip_id, cls, severity == cls))

    import pandas as pd

    from firewatch.io.rle import encode_rle

    pd.DataFrame(meta_rows).to_csv(output / "meta.csv", index=False)
    pd.DataFrame(sample_rows).to_csv(output / "sample_submission.csv", index=False)
    truth_frame = pd.DataFrame([{"chip_id": cid, "class_id": cls, "rle": encode_rle(mask)}
                                for cid, cls, mask in truth_rows])
    truth_frame.to_csv(output / "truth.csv", index=False)
    print(f"создано: {n_af} AF-чипов и {n_bs} BS-чипов в {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синтетический набор в структуре данных кейса")
    parser.add_argument("--output", required=True, help="каталог для записи")
    parser.add_argument("--n-af", type=int, default=6)
    parser.add_argument("--n-bs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--split", default="tr", choices=["tr", "te"],
                        help="tr — с эталонными масками, te — тестовая часть")
    parser.add_argument("--no-georeference", action="store_true",
                        help="записать чипы без геопривязки, как в обезличенном тесте")
    args = parser.parse_args(argv)
    generate(Path(args.output), args.n_af, args.n_bs, args.seed, args.split, not args.no_georeference)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
