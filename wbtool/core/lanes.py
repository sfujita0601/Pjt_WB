"""レーン位置の半自動「候補提示」。結果は必ずユーザーが確認・修正する前提。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from ..models.project import Rect
from .quant import polarity_sign


@dataclass
class LaneSuggestion:
    rects: list[Rect]
    method: str          # "peaks" | "equal_spacing"
    message: str


def suggest_lanes(image: np.ndarray, n_lanes: int, polarity: str,
                  y0: int = 0, y1: int | None = None, x0: int = 0, x1: int | None = None,
                  width_fraction: float = 0.7) -> LaneSuggestion:
    """列方向プロファイルのピークからレーン中心の候補を求める。

    指定数のピークが見つからない場合は、指定範囲の等間隔配置にフォールバックする。
    """
    if n_lanes < 1:
        raise ValueError("レーン数は1以上を指定してください。")
    h, w = image.shape
    y1 = h if y1 is None else min(y1, h)
    x1 = w if x1 is None else min(x1, w)
    y0, x0 = max(y0, 0), max(x0, 0)
    if y1 - y0 < 1 or x1 - x0 < n_lanes:
        raise ValueError("レーン検出の範囲が狭すぎます。")
    block = image[y0:y1, x0:x1].astype(np.float64)
    col = polarity_sign(polarity) * block.mean(axis=0)
    col = col - np.median(col)
    spacing = (x1 - x0) / n_lanes
    smooth = gaussian_filter1d(col, sigma=max(spacing / 8, 1.0))
    peaks, props = find_peaks(smooth, distance=max(int(spacing * 0.6), 1),
                              prominence=max(np.std(smooth) * 0.3, 1e-9))
    lane_w = max(int(round(spacing * width_fraction)), 1)
    if len(peaks) >= n_lanes:
        order = np.argsort(props["prominences"])[::-1][:n_lanes]
        centers = np.sort(peaks[order]) + x0
        method = "peaks"
        msg = f"列方向プロファイルのピークから {n_lanes} レーンの候補を提示しました。位置を必ず目視で確認してください。"
    else:
        centers = x0 + (np.arange(n_lanes) + 0.5) * spacing
        method = "equal_spacing"
        msg = (f"明瞭なピークが {len(peaks)} 個しか見つからなかったため、指定範囲を等間隔に {n_lanes} 分割しました。"
               "各レーンをドラッグして位置を合わせてください。")
    rects = [Rect(int(round(c - lane_w / 2)), y0, lane_w, y1 - y0).clipped(w, h) for c in centers]
    return LaneSuggestion(rects, method, msg)
