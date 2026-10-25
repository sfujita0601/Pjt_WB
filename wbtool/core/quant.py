"""バンド定量（GUI 非依存）。

定義（docs/theory_ja.md と一致させること）
-----------------------------------------
ROI 内の画素値を p_i（原画像の値、i = 1..N）、背景推定値を b（ROI 内で一定）、
極性に応じた符号を s（暗いバンド：-1、明るいバンド：+1）とすると

    S = Σ_i s · (p_i − b) = s · (Σ_i p_i − N·b)

- 補正前積分値   raw_sum = Σ p_i
- 背景積分値     bg_sum  = N · b
- 背景補正後積分値 S（負になっても 0 に丸めない）
- 「背景補正なし」は b を理論上の端値とする：暗いバンドでは白（dtype の最大値）、
  明るいバンドでは 0。すなわち暗いバンドでは S = Σ (max − p_i)。

いずれも較正されていない「相対的な画像強度」であり、光学密度やタンパク質量ではない。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models.project import (
    BG_LOCAL, BG_MANUAL, BG_NONE, POLARITY_BRIGHT, POLARITY_DARK, Rect,
)
from .imageio import dtype_max


def polarity_sign(polarity: str) -> int:
    if polarity == POLARITY_DARK:
        return -1
    if polarity == POLARITY_BRIGHT:
        return +1
    raise ValueError(f"不明な極性: {polarity}")


def _pixels(image: np.ndarray, rect: Rect) -> np.ndarray:
    h, w = image.shape
    r = rect.clipped(w, h)
    return image[r.y:r.y2, r.x:r.x2].astype(np.float64)


@dataclass
class BackgroundEstimate:
    method: str
    value: float
    n_pixels: int
    rects: list[Rect] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def local_background_rects(band: Rect, gap: int, height: int, width: int, img_height: int) -> list[Rect]:
    """同一レーン内、バンドROIの真上と真下に置く背景帯（バンドと同じ x・幅、画像内にクリップ）。"""
    above = Rect(band.x, band.y - gap - height, band.w, height).clipped(width, img_height)
    below = Rect(band.x, band.y2 + gap, band.w, height).clipped(width, img_height)
    return [r for r in (above, below) if r.area > 0]


def estimate_background(image: np.ndarray, band: Rect, method: str, polarity: str, *,
                        manual_rois: list[Rect] | None = None,
                        gap: int = 3, height: int = 5) -> BackgroundEstimate:
    img_h, img_w = image.shape
    if method == BG_NONE:
        value = float(dtype_max(image.dtype)) if polarity == POLARITY_DARK else 0.0
        return BackgroundEstimate(method, value, 0, [], {"reference": "white" if polarity == POLARITY_DARK else "zero"})
    if method == BG_MANUAL:
        rois = [r.clipped(img_w, img_h) for r in (manual_rois or [])]
        rois = [r for r in rois if r.area > 0]
        if not rois:
            return BackgroundEstimate(method, float("nan"), 0, [], {},
                                      ["手動背景ROIがありません。背景ROIを追加してください。"])
        vals = np.concatenate([_pixels(image, r).ravel() for r in rois])
        return BackgroundEstimate(method, float(vals.mean()), int(vals.size), rois, {"n_rois": len(rois)})
    if method == BG_LOCAL:
        rects = local_background_rects(band, gap, height, img_w, img_h)
        params = {"gap": gap, "height": height}
        if not rects:
            return BackgroundEstimate(method, float("nan"), 0, [], params,
                                      ["バンド近傍の背景領域が画像外です。ROI の位置か背景パラメータを見直してください。"])
        vals = np.concatenate([_pixels(image, r).ravel() for r in rects])
        warns = []
        if len(rects) < 2:
            warns.append("上下どちらかの背景帯が画像外のため、片側のみで背景を推定しました。")
        return BackgroundEstimate(method, float(vals.mean()), int(vals.size), rects, params, warns)
    raise ValueError(f"不明な背景推定法: {method}")


@dataclass
class BandMeasurement:
    n_pixels: int
    raw_sum: float
    raw_mean: float
    bg_value: float
    bg_sum: float
    sign: int
    corrected: float          # S
    n_low_extreme: int        # 画素値 = 0 の数
    n_high_extreme: int       # 画素値 = dtype 最大値 の数
    clipped: bool             # ROI が画像外にはみ出していたか


def measure_band(image: np.ndarray, rect: Rect, polarity: str, bg_value: float) -> BandMeasurement:
    img_h, img_w = image.shape
    r = rect.clipped(img_w, img_h)
    p = _pixels(image, rect)
    n = int(p.size)
    s = polarity_sign(polarity)
    raw_sum = float(p.sum()) if n else 0.0
    bg_sum = bg_value * n
    corrected = s * (raw_sum - bg_sum) if n else float("nan")
    vmax = dtype_max(image.dtype)
    return BandMeasurement(
        n_pixels=n,
        raw_sum=raw_sum,
        raw_mean=raw_sum / n if n else float("nan"),
        bg_value=float(bg_value),
        bg_sum=float(bg_sum),
        sign=s,
        corrected=float(corrected),
        n_low_extreme=int((p <= 0).sum()),
        n_high_extreme=int((p >= vmax).sum()),
        clipped=(r.as_tuple() != rect.as_tuple()),
    )


def lane_profile(image: np.ndarray, lane: Rect) -> tuple[np.ndarray, np.ndarray]:
    """レーン内の各行について、列方向の平均画素値を返す（y 座標, 平均値）。"""
    img_h, img_w = image.shape
    r = lane.clipped(img_w, img_h)
    if r.area == 0:
        return np.array([]), np.array([])
    block = image[r.y:r.y2, r.x:r.x2].astype(np.float64)
    return np.arange(r.y, r.y2), block.mean(axis=1)


def profile_peak_area(ys: np.ndarray, values: np.ndarray, y_start: int, y_end: int,
                      polarity: str) -> tuple[float, np.ndarray, np.ndarray]:
    """参考値：プロファイル上で y_start〜y_end-1 の範囲を、両端を結ぶ直線ベースラインより上（極性考慮）の面積。

    単位は「行平均画素値 × 行数」。矩形ROI積分 S とは定義が異なる（ImageJ Gel Analyzer の
    ピーク面積に近い考え方）。返り値：(面積, 範囲の y, ベースライン値)
    """
    mask = (ys >= y_start) & (ys < y_end)
    if mask.sum() < 2:
        return float("nan"), np.array([]), np.array([])
    yy, vv = ys[mask], values[mask]
    base = np.linspace(vv[0], vv[-1], vv.size)
    area = float((polarity_sign(polarity) * (vv - base)).sum())
    return area, yy, base
