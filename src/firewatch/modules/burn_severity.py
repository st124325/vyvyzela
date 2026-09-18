"""Модуль 2 — контур гари и степень поражения растительного покрова.

Контур строится по dNBR между датами «до» и «после», но единый порог на чип,
где соседствуют лес, степь и пашня, заведомо ошибочен: запас биомассы в степи
на порядок меньше, и dNBR физически не достигает лесных значений. Поэтому
пороги задаются по типу земного покрова (шкала USGS для леса и кустарника,
калиброванные по региону значения для травяного покрова, пашни и поймы).

Дополнительно решаются три прикладные проблемы: фенология (убранное или
вспаханное поле спектрально похоже на гарь), облачность и тени (оптика в этих
пикселях непригодна — контур восстанавливается по радиолокации) и рваный край
растровой маски (морфологическая постобработка).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from ..config import Config
from ..features.indices import dnbr as delta_nbr
from ..features.indices import nbr, nbr2, ndvi, rdnbr
from ..features.morphology import fill_small_holes, remove_small_objects, smooth_mask
from ..io.chips import Chip

LOGGER = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = (
    "dnbr", "rdnbr", "nbr_pre", "nbr_post",
    "dndvi", "ndvi_pre", "ndvi_post", "dnbr2",
    "b8a_pre", "b8a_post", "b12_pre", "b12_post", "b4_post",
    "dvh", "dvv", "vh_post", "vv_post",
    "landcover", "dem", "slope", "aspect",
    "optical_valid",
)

SEVERITY_CLASSES = (1, 2, 3)


@dataclass
class BurnSeverityResult:
    """Маска 0–3 и слои, по которым она получена."""

    severity: np.ndarray
    burned: np.ndarray
    optical_valid: np.ndarray
    features: dict[str, np.ndarray] = field(default_factory=dict)
    source: str = "rules"

    def class_masks(self) -> dict[int, np.ndarray]:
        return {cls: self.severity == cls for cls in SEVERITY_CLASSES}


def _optical(chip: Chip, role: str, config: Config) -> dict[str, np.ndarray]:
    """Каналы Sentinel-2 одной даты, приведённые к отражению 0–1."""
    mapping = config.require("data.s2_bands")
    scale = float(config.require("data.s2_scale"))
    bands: dict[str, np.ndarray] = {}
    for name in ("b2", "b3", "b4", "b5", "b6", "b7", "b8a", "b11", "b12"):
        band = chip.named_band(role, mapping, name)
        if band is not None:
            bands[name] = band / scale
    scl = chip.named_band(role, mapping, "scl")
    if scl is not None:
        bands["scl"] = scl
    return bands


def _sar(chip: Chip, role: str, config: Config) -> dict[str, np.ndarray]:
    """Каналы Sentinel-1 одной даты в дБ."""
    mapping = config.require("data.s1_bands")
    scale = float(config.require("data.s1_scale"))
    bands: dict[str, np.ndarray] = {}
    for name in ("vv", "vh"):
        band = chip.named_band(role, mapping, name)
        if band is not None:
            bands[name] = band / scale
    return bands


def _landcover_thresholds(landcover: np.ndarray, config: Config
                          ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Карты порогов t1/t2/t3 и маску пашни, собранные по типу земного покрова."""
    groups = config.require("burn_severity.landcover_groups")
    thresholds = config.require("burn_severity.thresholds")
    default = thresholds.get("default", {"t1": 0.08, "t2": 0.25, "t3": 0.5})

    codes = landcover.astype(np.int32)
    t1 = np.full(codes.shape, float(default["t1"]), dtype=np.float32)
    t2 = np.full(codes.shape, float(default["t2"]), dtype=np.float32)
    t3 = np.full(codes.shape, float(default["t3"]), dtype=np.float32)
    for group, classes in groups.items():
        values = thresholds.get(group)
        if not values:
            continue
        selector = np.isin(codes, [int(c) for c in classes])
        t1[selector], t2[selector], t3[selector] = (
            float(values["t1"]), float(values["t2"]), float(values["t3"]))
    cropland = np.isin(codes, [int(c) for c in groups.get("crop", [])])
    return t1, t2, t3, cropland


def compute_features(chip: Chip, config: Config) -> dict[str, np.ndarray]:
    """Слои-признаки BS-чипа: индексы по обеим датам, их разности, SAR и рельеф."""
    pre = _optical(chip, "pre", config)
    post = _optical(chip, "post", config)
    if "b8a" not in pre or "b8a" not in post or "b12" not in pre or "b12" not in post:
        raise ValueError(f"чип {chip.chip_id}: нет каналов B8A/B12 на обе даты")

    aux_map = config.require("data.bs_aux_bands")
    shape = chip.shape

    def aux(name: str, fallback: float) -> np.ndarray:
        band = chip.named_band("aux", aux_map, name)
        return band if band is not None else np.full(shape, fallback, dtype=np.float32)

    nbr_pre, nbr_post = nbr(pre["b8a"], pre["b12"]), nbr(post["b8a"], post["b12"])
    difference = delta_nbr(nbr_pre, nbr_post)
    ndvi_pre = ndvi(pre["b8a"], pre.get("b4", pre["b8a"]))
    ndvi_post = ndvi(post["b8a"], post.get("b4", post["b8a"]))

    scl_invalid = set(int(c) for c in config.require("burn_severity.scl.invalid_classes"))
    water_classes = set(int(c) for c in config.require("burn_severity.scl.water_classes"))
    optical_valid = np.isfinite(difference)
    water = np.zeros(shape, dtype=bool)
    for bands in (pre, post):
        scl = bands.get("scl")
        if scl is None:
            continue
        codes = scl.astype(np.int32)
        optical_valid &= ~np.isin(codes, list(scl_invalid))
        water |= np.isin(codes, list(water_classes))

    sar_pre, sar_post = _sar(chip, "s1_pre", config), _sar(chip, "s1_post", config)
    has_sar = "vh" in sar_pre and "vh" in sar_post
    delta_vh = (sar_post["vh"] - sar_pre["vh"]) if has_sar else np.zeros(shape, np.float32)
    delta_vv = (sar_post["vv"] - sar_pre["vv"]) if ("vv" in sar_pre and "vv" in sar_post) \
        else np.zeros(shape, np.float32)

    features = {
        "dnbr": difference,
        "rdnbr": rdnbr(difference, nbr_pre),
        "nbr_pre": nbr_pre,
        "nbr_post": nbr_post,
        "dndvi": ndvi_pre - ndvi_post,
        "ndvi_pre": ndvi_pre,
        "ndvi_post": ndvi_post,
        "dnbr2": (nbr2(pre["b11"], pre["b12"]) - nbr2(post["b11"], post["b12"]))
        if ("b11" in pre and "b11" in post) else np.zeros(shape, np.float32),
        "b8a_pre": pre["b8a"], "b8a_post": post["b8a"],
        "b12_pre": pre["b12"], "b12_post": post["b12"],
        "b4_post": post.get("b4", np.zeros(shape, np.float32)),
        "dvh": delta_vh, "dvv": delta_vv,
        "vh_post": sar_post.get("vh", np.zeros(shape, np.float32)),
        "vv_post": sar_post.get("vv", np.zeros(shape, np.float32)),
        "landcover": aux("landcover", 0.0),
        "dem": aux("dem", 0.0),
        "slope": aux("slope", 0.0),
        "aspect": aux("aspect", 0.0),
        "optical_valid": optical_valid.astype(np.float32),
    }
    features["_optical_valid"] = optical_valid
    features["_water"] = water
    features["_has_sar"] = has_sar
    return features


def _rule_decision(features: dict[str, np.ndarray], config: Config) -> np.ndarray:
    """Контур и степень поражения по калиброванным порогам dNBR."""
    params = config.require("burn_severity")
    confirm = params["confirm"]
    sar_params = params["sar"]

    t1, t2, t3, cropland = _landcover_thresholds(features["landcover"], config)
    difference = features["dnbr"]
    optical_valid = features["_optical_valid"] & ~features["_water"]

    # Базовое условие: падение NBR выше порога своего типа покрова.
    burned = optical_valid & (difference >= t1)

    # Подтверждение: гарь обязана показать падение зелёности и наличие биомассы
    # до пожара. Это отсекает вспаханное поле и голый грунт.
    burned &= features["dndvi"] >= float(confirm["min_dndvi"])
    burned &= features["nbr_pre"] >= float(confirm["min_nbr_pre"])

    # Пашня — главный источник ложных контуров: убранное поле спектрально
    # похоже на гарь, поэтому здесь требования строже.
    crop_strict = (difference >= t1 + float(confirm["crop_extra_dnbr"])) & (
        features["dndvi"] >= float(confirm["crop_min_dndvi"]))
    burned &= ~(cropland & ~crop_strict)

    # Радиолокация: подтверждает контур там, где оптика есть, и восстанавливает
    # его там, где пиксель закрыт облаком или тенью.
    if bool(sar_params["enabled"]) and features["_has_sar"]:
        recovered = (~features["_optical_valid"]) & ~features["_water"] & (
            features["dvh"] <= float(sar_params["recover_dvh_db"]))
        burned |= recovered

    burned = remove_small_objects(burned, int(params["morphology"]["min_object_px"]))
    burned = fill_small_holes(burned, int(params["morphology"]["fill_holes_px"]))
    burned = smooth_mask(burned, int(params["morphology"]["smooth_iterations"]))

    severity = np.zeros(burned.shape, dtype=np.uint8)
    severity[burned] = 1
    severity[burned & (difference >= t2)] = 2
    severity[burned & (difference >= t3)] = 3

    # Пиксели, попавшие в контур по радиолокации или после заполнения дыр,
    # класс по dNBR получить не могут — им назначается класс по умолчанию.
    unresolved = burned & ~features["_optical_valid"]
    severity[unresolved] = int(sar_params["recover_severity"])
    return severity


def stack_features(features: dict[str, np.ndarray], selection: np.ndarray) -> np.ndarray:
    columns = [np.nan_to_num(np.asarray(features[name], dtype=np.float32)[selection],
                             nan=0.0, posinf=0.0, neginf=0.0)
               for name in FEATURE_NAMES]
    return np.stack(columns, axis=1)


def _smooth_labels(severity: np.ndarray, config: Config) -> np.ndarray:
    """Медианный фильтр по классам внутри контура — убирает «соль» из классов."""
    if not severity.any():
        return severity
    smoothed = ndimage.median_filter(severity, size=3, mode="nearest")
    burned = severity > 0
    result = np.zeros_like(severity)
    # Класс сглаживаем только внутри уже найденного контура, границу не двигаем.
    result[burned] = np.maximum(smoothed[burned], 1)
    return result


def segment(chip: Chip, config: Config, model=None) -> BurnSeverityResult:
    """Возвращает маску 0–3 (0 — не горело, 1/2/3 — степень поражения)."""
    features = compute_features(chip, config)
    severity = _rule_decision(features, config)
    source = "rules"

    if model is not None:
        params = config.require("burn_severity")
        # Модель вызывается не на всех пикселях, а только на спорных: там, где
        # падение NBR хотя бы приближается к порогу своего типа покрова, либо
        # где оптика непригодна и контур восстанавливается по радиолокации.
        # Пиксели с околонулевым dNBR гарью быть не могут, и их пропуск
        # сокращает время инференса примерно на порядок.
        t1, _, _, _ = _landcover_thresholds(features["landcover"], config)
        scale = float(config.get_path("burn_severity.model.candidate_scale", 0.5))
        candidate = features["_optical_valid"] & (features["dnbr"] >= t1 * scale)
        if bool(params["sar"]["enabled"]) and features["_has_sar"]:
            candidate |= (~features["_optical_valid"]) & (
                features["dvh"] <= float(params["sar"]["recover_dvh_db"]))
        candidate = np.asarray(candidate) & ~features["_water"]
        predicted = np.zeros_like(severity)
        if np.any(candidate):
            matrix = stack_features(features, candidate)
            predicted[candidate] = model.predict(matrix).astype(np.uint8)
        burned = predicted > 0
        burned = remove_small_objects(burned, int(params["morphology"]["min_object_px"]))
        burned = fill_small_holes(burned, int(params["morphology"]["fill_holes_px"]))
        predicted[~burned] = 0
        # Пиксели, добавленные заполнением дыр, берут класс из правил или класс 1.
        added = burned & (predicted == 0)
        predicted[added] = np.maximum(severity[added], 1)
        severity, source = predicted, "model"

    severity = _smooth_labels(severity, config)
    return BurnSeverityResult(severity=severity, burned=severity > 0,
                              optical_valid=features["_optical_valid"],
                              features=features, source=source)
