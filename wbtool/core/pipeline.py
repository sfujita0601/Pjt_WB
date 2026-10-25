"""プロジェクト全体の測定（画像ごとのバンド定量）を行う。GUI 非依存。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from ..models.project import BG_LABELS, BandROI, ImageRecord, Project
from .imageio import LoadedImage, load_image
from .quant import estimate_background, measure_band

MEASUREMENT_COLUMNS = [
    "image_id", "band_id", "lane_id", "sample_id", "group", "bio_rep", "tech_rep", "protein",
    "experiment_id", "blot_id", "membrane_id", "exposure_id", "image_included", "excluded", "exclude_reason",
    "polarity", "sign", "roi_x", "roi_y", "roi_w", "roi_h", "n_pixels",
    "raw_sum", "raw_mean", "bg_method", "bg_method_label", "bg_value", "bg_sum", "bg_n_pixels", "bg_rects",
    "corrected", "n_low_extreme", "n_high_extreme", "roi_clipped", "warnings",
]


class ImageCache:
    """読み込んだ原画像をメモリに保持する（読み取り専用配列）。"""

    def __init__(self, loader: Callable[[str], LoadedImage] = load_image):
        self._loader = loader
        self._cache: dict[str, LoadedImage] = {}

    def get(self, path: str) -> LoadedImage:
        key = str(Path(path))
        if key not in self._cache:
            self._cache[key] = self._loader(key)
        return self._cache[key]

    def put(self, path: str, img: LoadedImage) -> None:
        self._cache[str(Path(path))] = img

    def drop(self, path: str) -> None:
        self._cache.pop(str(Path(path)), None)

    def clear(self) -> None:
        self._cache.clear()


def measure_one(project: Project, im: ImageRecord, band: BandROI, data: np.ndarray) -> dict:
    lane = im.lane(band.lane_id)
    method = im.band_bg_method(band)
    bg = estimate_background(data, band.rect, method, im.polarity, manual_rois=im.bg_rois,
                             gap=im.bg_gap, height=im.bg_height)
    m = measure_band(data, band.rect, im.polarity, bg.value)
    sample_id = lane.sample_id if lane else ""
    s = project.sample(sample_id) if sample_id else None
    warns = list(bg.warnings)
    if m.clipped:
        warns.append("ROI が画像外にはみ出しています（画像内の部分のみ測定）。")
    if lane is None:
        warns.append("バンドがレーンに属していません。")
    return {
        "image_id": im.image_id, "band_id": band.band_id, "lane_id": band.lane_id,
        "sample_id": sample_id, "group": s.group if s else "", "bio_rep": s.bio_rep if s else "",
        "tech_rep": s.tech_rep if s else "", "protein": im.band_protein(band),
        "experiment_id": im.experiment_id, "blot_id": im.blot_id, "membrane_id": im.membrane_id,
        "exposure_id": im.exposure_id, "image_included": im.include,
        "excluded": band.excluded, "exclude_reason": band.exclude_reason,
        "polarity": im.polarity, "sign": m.sign,
        "roi_x": band.rect.x, "roi_y": band.rect.y, "roi_w": band.rect.w, "roi_h": band.rect.h,
        "n_pixels": m.n_pixels, "raw_sum": m.raw_sum, "raw_mean": m.raw_mean,
        "bg_method": method, "bg_method_label": BG_LABELS.get(method, method),
        "bg_value": m.bg_value, "bg_sum": m.bg_sum, "bg_n_pixels": bg.n_pixels,
        "bg_rects": ";".join(f"{r.x},{r.y},{r.w},{r.h}" for r in bg.rects),
        "corrected": m.corrected, "n_low_extreme": m.n_low_extreme, "n_high_extreme": m.n_high_extreme,
        "roi_clipped": m.clipped, "warnings": " / ".join(warns),
    }


@dataclass
class MeasureResult:
    table: pd.DataFrame
    errors: dict[str, str] = field(default_factory=dict)   # image_id -> 読込エラー


def measure_project(project: Project, cache: ImageCache) -> MeasureResult:
    rows, errors = [], {}
    for im in project.images:
        if not im.bands:
            continue
        try:
            data = cache.get(im.path).data
        except Exception as e:  # noqa: BLE001
            errors[im.image_id] = str(e)
            continue
        for band in im.bands:
            rows.append(measure_one(project, im, band, data))
    return MeasureResult(pd.DataFrame(rows, columns=MEASUREMENT_COLUMNS), errors)


def compare_with_saved(current: pd.DataFrame, saved: list[dict], rtol: float = 1e-9) -> list[str]:
    """保存済み測定値と再計算値の照合。差異の説明リストを返す（空なら一致）。"""
    if not saved:
        return []
    old = pd.DataFrame(saved)
    diffs = []
    key = ["image_id", "band_id"]
    if current.empty:
        return ["再計算した測定値がありません（画像を読み込めない可能性があります）。"]
    merged = old.merge(current, on=key, how="outer", suffixes=("_saved", "_now"), indicator=True)
    for _, r in merged.iterrows():
        if r["_merge"] != "both":
            diffs.append(f"{r['image_id']}/{r['band_id']}: 保存時と再計算時でバンドの有無が異なります。")
            continue
        for col in ("raw_sum", "bg_value", "corrected"):
            a, b = r[f"{col}_saved"], r[f"{col}_now"]
            if not (pd.isna(a) and pd.isna(b)) and not np.isclose(a, b, rtol=rtol, atol=1e-9):
                diffs.append(f"{r['image_id']}/{r['band_id']}: {col} が一致しません（保存 {a}, 現在 {b}）。")
    return diffs


def results_to_records(df: pd.DataFrame) -> list[dict]:
    return [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in rec.items()}
            for rec in df.to_dict(orient="records")]


__all__ = ["ImageCache", "measure_project", "measure_one", "MeasureResult", "compare_with_saved",
           "results_to_records", "MEASUREMENT_COLUMNS"]
