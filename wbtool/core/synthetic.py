"""教材用合成データ（実験由来ではない）。正解値が計算できる人工ブロット画像を作る。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter

from ..models.project import Rect


@dataclass
class SyntheticBlot:
    data: np.ndarray
    lanes: list[Rect]
    bands: list[Rect]
    amplitudes: list[float]          # 各バンドの背景からの深さ（画素値）
    true_signal: list[float]         # 背景を正確に引いたときの S の正解（ぼかし無しの場合）
    background: float
    description: str = "教材用合成データ（実験由来ではありません）"
    meta: dict = field(default_factory=dict)


def make_blot(amplitudes, *, width: int | None = None, height: int = 120, lane_width: int = 30,
              lane_gap: int = 14, band_height: int = 10, band_y: int | None = None,
              background: float = 200.0, gradient: float = 0.0, noise_sd: float = 0.0,
              blur_sigma: float = 0.0, dtype=np.uint8, polarity: str = "dark", seed: int = 0,
              band_width: int | None = None) -> SyntheticBlot:
    """矩形バンドを持つブロット画像を作る。

    - polarity="dark"：背景 background から amplitude だけ暗いバンド（フィルムのスキャン像）
    - gradient：y 方向の背景傾斜（画素値／行）。上下対称の近傍背景なら線形傾斜は正確に打ち消される
    - 値は dtype の範囲にクリップされる（クリップされた場合は飽和の疑似例になる）
    """
    amps = [float(a) for a in amplitudes]
    n = len(amps)
    margin = lane_gap
    width = width or (margin * 2 + n * lane_width + (n - 1) * lane_gap)
    band_y = band_y if band_y is not None else height // 2 - band_height // 2
    bw = band_width or lane_width - 8
    yy = np.arange(height, dtype=np.float64)[:, None]
    img = np.full((height, width), background, dtype=np.float64) + gradient * (yy - height / 2)
    lanes, bands = [], []
    sign = -1.0 if polarity == "dark" else 1.0
    signal_layer = np.zeros_like(img)
    for i, a in enumerate(amps):
        x = margin + i * (lane_width + lane_gap)
        lanes.append(Rect(x, 0, lane_width, height))
        bx = x + (lane_width - bw) // 2
        bands.append(Rect(bx, band_y, bw, band_height))
        signal_layer[band_y:band_y + band_height, bx:bx + bw] = a
    if blur_sigma > 0:
        signal_layer = gaussian_filter(signal_layer, blur_sigma)
    img = img + sign * signal_layer
    if noise_sd > 0:
        img = img + np.random.default_rng(seed).normal(0, noise_sd, img.shape)
    info = np.iinfo(dtype)
    data = np.clip(np.rint(img), info.min, info.max).astype(dtype)
    true = [a * b.area for a, b in zip(amps, bands)]
    return SyntheticBlot(data, lanes, bands, amps, true, background,
                         meta={"gradient": gradient, "noise_sd": noise_sd, "blur_sigma": blur_sigma,
                               "polarity": polarity, "dtype": np.dtype(dtype).name})


def example_dataset(seed: int = 1) -> dict:
    """学習用の Target / Loading control 画像一式（8 レーン：Control×4, Treated×4）。

    真の比（Target量/LC量）：Control ≈ 1.0、Treated ≈ 2.0（biological ばらつきあり）。
    """
    rng = np.random.default_rng(seed)
    lc_load = np.array([1.0, 0.85, 1.15, 0.95, 1.05, 0.9, 1.1, 1.0])      # ロード量のばらつき
    true_ratio = np.r_[rng.normal(1.0, 0.12, 4), rng.normal(2.0, 0.25, 4)]
    target_amp = 9000 * lc_load * true_ratio / 2.0
    lc_amp = 12000 * lc_load
    common = dict(height=160, lane_width=34, lane_gap=16, band_height=12, background=52000.0,
                  gradient=-15.0, noise_sd=300.0, blur_sigma=1.5, dtype=np.uint16)
    target = make_blot(target_amp, band_y=70, seed=seed, **common)
    lc = make_blot(lc_amp, band_y=74, seed=seed + 1, **common)
    # 過露光の疑似例：同じ膜の長時間露光（飽和で頭打ち）
    over_amp = target_amp * 6
    over = make_blot(over_amp, band_y=70, seed=seed + 2, **{**common, "background": 50000.0})
    # 独立2群のデザイン：bio_rep は群ごとに別 ID（C1–C4, T1–T4）。technical replicate は各1
    samples = [(f"S{i + 1}", "Control" if i < 4 else "Treated", f"{'C' if i < 4 else 'T'}{i % 4 + 1}", "1")
               for i in range(8)]
    return {"target": target, "loading": lc, "target_overexposed": over, "samples": samples,
            "true_ratio": true_ratio, "lc_load": lc_load}


def example_dataset_3groups(seed: int = 7) -> dict:
    """3群（Control・DrugA・DrugB、各4生物学的反復）の教材用データ。真の比：1.0 / 1.5 / 2.5（ばらつきあり）。"""
    rng = np.random.default_rng(seed)
    groups = ["Control"] * 4 + ["DrugA"] * 4 + ["DrugB"] * 4
    centers = {"Control": 1.0, "DrugA": 1.5, "DrugB": 2.5}
    sds = {"Control": 0.10, "DrugA": 0.15, "DrugB": 0.35}
    true_ratio = np.array([rng.normal(centers[g], sds[g]) for g in groups])
    lc_load = rng.uniform(0.85, 1.15, len(groups))
    target_amp = 6000 * lc_load * true_ratio / 2.5
    lc_amp = 12000 * lc_load
    common = dict(height=160, lane_width=30, lane_gap=12, band_height=12, background=52000.0,
                  gradient=-15.0, noise_sd=300.0, blur_sigma=1.5, dtype=np.uint16)
    target = make_blot(target_amp, band_y=70, seed=seed, **common)
    lc = make_blot(lc_amp, band_y=74, seed=seed + 1, **common)
    prefix = {"Control": "C", "DrugA": "A", "DrugB": "B"}
    samples = [(f"S{i + 1}", g, f"{prefix[g]}{i % 4 + 1}", "1") for i, g in enumerate(groups)]
    return {"target": target, "loading": lc, "samples": samples, "true_ratio": true_ratio, "lc_load": lc_load}
