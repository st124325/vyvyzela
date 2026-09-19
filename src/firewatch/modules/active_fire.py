"""Модуль 1 — детекция пикселей активного природного горения по VIIRS I1–I5.

Логика повторяет контекстную схему детекции: сначала отбираются кандидаты по
мягкому абсолютному порогу, затем каждый кандидат сравнивается с фоном в окне,
из которого исключены сами кандидаты и невалидные пиксели. Отдельный блок
отбраковывает четыре описанных в постановке источника ложных срабатываний —
техногенные термоаномалии, солнечный блик, нагретый обнажённый грунт и край
облака. Если в репозитории есть обученные веса, окончательное решение по
кандидату принимает модель, использующая те же признаки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from ..config import Config
from ..features.indices import context_stats, ndvi
from ..features.morphology import dilate, remove_small_objects
from ..io.chips import Chip

LOGGER = logging.getLogger(__name__)

# Порядок признаков зафиксирован: обучение и инференс используют один список.
FEATURE_NAMES: tuple[str, ...] = (
    "i1", "i2", "i3", "i4", "i5",
    "dt", "ndvi",
    "i4_excess", "dt_excess",
    "bg_i4_mad", "bg_dt_mad", "bg_dt_std", "bg_count",
    "landcover", "solar_zenith", "dem", "t2m", "rh", "wind",
    "is_night",
)


@dataclass
class ActiveFireResult:
    """Результат работы модуля: маска горения и диагностические слои."""

    mask: np.ndarray
    candidates: np.ndarray
    valid: np.ndarray
    features: dict[str, np.ndarray] = field(default_factory=dict)
    rule_mask: np.ndarray | None = None
    source: str = "rules"   # какое решающее правило дало итоговую маску


def _layer(chip: Chip, role: str, mapping: dict, name: str, fallback: float | None = None
           ) -> np.ndarray | None:
    band = chip.named_band(role, mapping, name)
    if band is not None:
        return band
    if fallback is None:
        return None
    return np.full(chip.shape, fallback, dtype=np.float32)


def compute_features(chip: Chip, config: Config) -> dict[str, np.ndarray]:
    """Считает все слои-признаки AF-чипа, включая контекстную статистику фона.

    Возвращает словарь со слоями из FEATURE_NAMES плюс служебные ``valid`` и
    ``candidates``. Одна и та же функция вызывается при обучении и при инференсе.
    """
    params = config.require("active_fire")
    band_map = config.require("data.af_bands")
    aux_map = config.require("data.af_aux_bands")

    i4 = _layer(chip, "main", band_map, "i4")
    i5 = _layer(chip, "main", band_map, "i5")
    if i4 is None or i5 is None:
        raise ValueError(f"чип {chip.chip_id}: нет каналов I4/I5, детекция невозможна")
    i1 = _layer(chip, "main", band_map, "i1", fallback=np.nan)
    i2 = _layer(chip, "main", band_map, "i2", fallback=np.nan)
    i3 = _layer(chip, "main", band_map, "i3", fallback=np.nan)

    landcover = _layer(chip, "aux", aux_map, "landcover", fallback=0.0)
    dem = _layer(chip, "aux", aux_map, "dem", fallback=0.0)
    t2m = _layer(chip, "aux", aux_map, "t2m", fallback=np.nan)
    rh = _layer(chip, "aux", aux_map, "rh", fallback=np.nan)
    wind = _layer(chip, "aux", aux_map, "wind", fallback=np.nan)
    # solar_zenith и valid могут лежать в aux-файле или прямо в основном VIIRS-растре
    # (после I1..I5) — зависит от релиза; пробуем оба места.
    solar_zenith = _layer(chip, "aux", aux_map, "solar_zenith")
    if solar_zenith is None:
        solar_zenith = _layer(chip, "main", band_map, "solar_zenith", fallback=np.nan)
    valid_layer = _layer(chip, "aux", aux_map, "valid")
    if valid_layer is None:
        valid_layer = _layer(chip, "main", band_map, "valid")

    valid = np.isfinite(i4) & np.isfinite(i5) & (i4 > 0) & (i5 > 0)
    if valid_layer is not None:
        valid &= valid_layer > 0

    delta_t = i4 - i5
    vegetation = ndvi(np.nan_to_num(i2), np.nan_to_num(i1))

    # Ночь определяем по зенитному углу Солнца; если угол не выдан — по тому,
    # что отражательные каналы ночью не несут сигнала.
    night_threshold = float(params["day_solar_zenith"])
    if np.isfinite(solar_zenith).any():
        is_night = solar_zenith > night_threshold
    else:
        is_night = np.broadcast_to(np.nanmean(np.nan_to_num(i2)) < 0.01, i4.shape).copy()
    night_fraction = float(np.mean(is_night))

    def by_daypart(section: str, key: str) -> float:
        """Порог дня/ночи: чип снят одним пролётом, поэтому берём преобладающее время."""
        node = params[section]["night" if night_fraction >= 0.5 else "day"]
        return float(node[key])

    saturation = float(params["saturation_i4"])
    candidates = valid & (
        ((i4 >= by_daypart("candidate", "i4")) & (delta_t >= by_daypart("candidate", "dt")))
        | (i4 >= saturation)
    )

    # Фон: валидные пиксели, не являющиеся кандидатами и не примыкающие к ним.
    background = valid & ~dilate(candidates, 1)
    window = int(params["window"])
    mean_i4, _, mad_i4, count = context_stats(i4, background, window)
    mean_dt, std_dt, mad_dt, _ = context_stats(delta_t, background, window)

    features = {
        "i1": i1, "i2": i2, "i3": i3, "i4": i4, "i5": i5,
        "dt": delta_t,
        "ndvi": vegetation,
        "i4_excess": i4 - mean_i4,
        "dt_excess": delta_t - mean_dt,
        "bg_i4_mad": mad_i4,
        "bg_dt_mad": mad_dt,
        "bg_dt_std": std_dt,
        "bg_count": count,
        "landcover": landcover,
        "solar_zenith": solar_zenith,
        "dem": dem,
        "t2m": t2m,
        "rh": rh,
        "wind": wind,
        "is_night": is_night.astype(np.float32),
    }
    features["valid"] = valid
    features["candidates"] = candidates
    features["mean_i4"] = mean_i4
    features["mean_dt"] = mean_dt
    return features


def _rule_decision(features: dict[str, np.ndarray], config: Config) -> np.ndarray:
    """Контекстный тест и отбраковка ложных срабатываний (решение без модели)."""
    params = config.require("active_fire")
    context = params["context"]
    reject = params["reject"]

    valid = features["valid"]
    candidates = features["candidates"]
    i4, delta_t = features["i4"], features["dt"]
    night = features["is_night"] > 0.5
    daypart = "night" if float(np.mean(night)) >= 0.5 else "day"

    enough_background = features["bg_count"] >= int(params["window"]) ** 2 * float(params["min_bg_fraction"])
    i4_margin = np.maximum(float(context["i4_mad_k"]) * features["bg_i4_mad"], float(context["i4_min_delta"]))
    dt_margin = np.maximum(float(context["dt_mad_k"]) * features["bg_dt_mad"], float(context["dt_min_delta"]))

    contextual = (
        candidates
        & enough_background
        & (features["i4_excess"] > i4_margin)
        & (features["dt_excess"] > dt_margin)
    )

    # Очень горячий пиксель детектируется без контекста: фон вокруг него может
    # быть целиком занят тем же пожаром, и контекстный тест тогда не срабатывает.
    absolute_node = params["absolute"][daypart]
    absolute = valid & (i4 >= float(absolute_node["i4"])) & (delta_t >= float(absolute_node["dt"]))
    absolute |= valid & (i4 >= float(params["saturation_i4"])) & (delta_t >= float(absolute_node["dt"]) * 0.5)

    detection = contextual | absolute

    # --- источники ложных срабатываний -------------------------------------
    # 1. Техногенные термоаномалии: факелы и промплощадки лежат на застройке.
    technogenic_classes = set(int(c) for c in reject["technogenic_landcover"])
    landcover_int = np.nan_to_num(features["landcover"], nan=-1.0).astype(np.int32)
    builtup = np.isin(landcover_int, list(technogenic_classes))
    detection &= ~dilate(builtup, int(reject["builtup_dilate"]))

    # 2. Солнечный блик: высокое SWIR-отражение при дневной съёмке.
    if daypart == "day":
        glint = np.nan_to_num(features["i3"], nan=0.0) >= float(reject["glint_i3"])
        detection &= ~glint
        # 3. Вода: отрицательный NDVI при низком ближнем ИК.
        water = (features["ndvi"] < float(reject["water_ndvi"])) & (
            np.nan_to_num(features["i2"], nan=1.0) < float(reject["water_i2"]))
        detection &= ~water
        # 4. Облако: яркий видимый канал при умеренной разности каналов.
        cloud = (np.nan_to_num(features["i1"], nan=0.0) > float(reject["cloud_i1"])) & (
            delta_t < float(reject["cloud_dt"]))
        detection &= ~cloud

    # 5. Край облака и дымовой шлейф: неоднородный фон в окне.
    unstable_background = features["bg_dt_std"] > float(reject["bg_dt_std"])
    detection &= ~(unstable_background & ~absolute)

    # 6. Нагретый грунт, карьер, вспаханное поле: на этих классах покрова
    #    требуем дополнительный запас по разности каналов.
    suspicious = np.isin(landcover_int, [int(c) for c in reject["suspicious_landcover"]])
    strict = features["dt_excess"] > dt_margin + float(reject["suspicious_dt_margin"])
    detection &= ~(suspicious & ~strict & ~absolute)

    return detection & valid


def stack_features(features: dict[str, np.ndarray], selection: np.ndarray) -> np.ndarray:
    """Матрица признаков (пиксели × FEATURE_NAMES) для выбранных пикселей."""
    columns = [np.nan_to_num(np.asarray(features[name], dtype=np.float32)[selection],
                             nan=0.0, posinf=0.0, neginf=0.0)
               for name in FEATURE_NAMES]
    return np.stack(columns, axis=1) if columns else np.empty((int(selection.sum()), 0), np.float32)


def detect(chip: Chip, config: Config, model=None) -> ActiveFireResult:
    """Возвращает бинарную маску активного природного горения для AF-чипа."""
    features = compute_features(chip, config)
    rule_mask = _rule_decision(features, config)
    mask, source = rule_mask, "rules"

    if model is not None:
        candidates = features["candidates"]
        mask = np.zeros_like(rule_mask)
        if candidates.any():
            matrix = stack_features(features, candidates)
            threshold = float(config.get_path("active_fire.model.threshold", 0.5))
            probability = model.predict_proba(matrix)[:, 1]
            mask[candidates] = probability >= threshold
        mask &= features["valid"]
        source = "model"

    minimum = int(config.get_path("active_fire.min_cluster_size", 1))
    mask = remove_small_objects(mask, minimum)
    return ActiveFireResult(mask=mask, candidates=features["candidates"], valid=features["valid"],
                            features=features, rule_mask=rule_mask, source=source)
