"""定量の境界値・既知解テスト（合成画像）。"""
import numpy as np
import pytest

from wbtool.core.quant import (
    estimate_background, lane_profile, local_background_rects, measure_band, polarity_sign, profile_peak_area,
)
from wbtool.core.synthetic import make_blot
from wbtool.models.project import BG_LOCAL, BG_MANUAL, BG_NONE, Rect


def test_polarity_sign():
    assert polarity_sign("dark") == -1
    assert polarity_sign("bright") == 1
    with pytest.raises(ValueError):
        polarity_sign("x")


@pytest.mark.parametrize("dtype,bg,amp", [(np.uint8, 200, 50), (np.uint16, 50000, 12000)])
def test_known_rectangle_band_exact(dtype, bg, amp):
    blot = make_blot([amp], background=bg, dtype=dtype, band_height=8, lane_width=20, height=60)
    band = blot.bands[0]
    m = measure_band(blot.data, band, "dark", float(bg))
    assert m.n_pixels == band.area
    assert m.raw_sum == (bg - amp) * band.area
    assert m.corrected == pytest.approx(amp * band.area)
    assert m.corrected == pytest.approx(blot.true_signal[0])


def test_16bit_values_not_reduced_to_8bit():
    blot = make_blot([30000], background=60000, dtype=np.uint16)
    m = measure_band(blot.data, blot.bands[0], "dark", 60000.0)
    assert m.raw_mean == 30000      # 8-bit に縮小されていれば 255 以下になる
    assert blot.data.dtype == np.uint16


def test_manual_rect_pixel_sum():
    img = np.arange(100, dtype=np.uint8).reshape(10, 10)
    r = Rect(2, 3, 4, 2)        # 行3〜4、列2〜5
    m = measure_band(img, r, "bright", 0.0)
    expected = img[3:5, 2:6].astype(float).sum()
    assert m.raw_sum == expected
    assert m.corrected == expected
    assert m.n_pixels == 8


def test_negative_not_clipped_to_zero():
    img = np.full((20, 20), 100, np.uint8)
    m = measure_band(img, Rect(5, 5, 4, 4), "dark", 90.0)   # バンドが背景より明るい（暗いバンドとして）
    assert m.corrected == pytest.approx(-160.0)


def test_polarity_switch_flips_sign_but_display_does_not_matter():
    blot = make_blot([60], background=200)
    d = measure_band(blot.data, blot.bands[0], "dark", 200.0)
    b = measure_band(blot.data, blot.bands[0], "bright", 200.0)
    assert d.corrected == pytest.approx(-b.corrected)
    assert d.raw_sum == b.raw_sum
    # 測定は常に原画像配列で行われる。表示用反転コピーを作っても原画像は変わらない
    from wbtool.core.export import display_copy
    before = blot.data.copy()
    display_copy(blot.data, invert=True)
    assert np.array_equal(before, blot.data)


def test_bright_polarity_image():
    blot = make_blot([500], background=1000, dtype=np.uint16, polarity="bright")
    m = measure_band(blot.data, blot.bands[0], "bright", 1000.0)
    assert m.corrected == pytest.approx(500 * blot.bands[0].area)


def test_background_none_uses_white_for_dark():
    img = np.full((10, 10), 250, np.uint8)
    bg = estimate_background(img, Rect(1, 1, 2, 2), BG_NONE, "dark")
    assert bg.value == 255
    m = measure_band(img, Rect(1, 1, 2, 2), "dark", bg.value)
    assert m.corrected == 4 * 5
    bg16 = estimate_background(img.astype(np.uint16), Rect(1, 1, 2, 2), BG_NONE, "dark")
    assert bg16.value == 65535
    assert estimate_background(img, Rect(1, 1, 2, 2), BG_NONE, "bright").value == 0


def test_local_background_linear_gradient_is_cancelled():
    blot = make_blot([80], background=180, gradient=0.5, height=100, band_height=10)
    band = blot.bands[0]
    bg = estimate_background(blot.data, band, BG_LOCAL, "dark", gap=3, height=5)
    assert len(bg.rects) == 2
    m = measure_band(blot.data, band, "dark", bg.value)
    # 丸め（np.rint）による誤差は1画素あたり 0.5 未満
    assert abs(m.corrected - blot.true_signal[0]) < 0.5 * band.area


def test_local_background_rect_geometry_and_clipping():
    band = Rect(10, 2, 5, 4)
    rects = local_background_rects(band, gap=1, height=3, width=50, img_height=20)
    # 上側は y=-2..0 → クリップされて y=0, h=1
    assert rects[0] == Rect(10, 0, 5, 1)
    assert rects[1] == Rect(10, 7, 5, 3)
    rects = local_background_rects(Rect(0, 0, 5, 20), 1, 3, 50, 20)
    assert rects == []


def test_manual_background_mean_and_missing():
    img = np.zeros((10, 10), np.uint8)
    img[0:2, 0:2] = 10
    img[8:10, 8:10] = 30
    bg = estimate_background(img, Rect(4, 4, 2, 2), BG_MANUAL, "dark", manual_rois=[Rect(0, 0, 2, 2), Rect(8, 8, 2, 2)])
    assert bg.value == 20 and bg.n_pixels == 8
    bg = estimate_background(img, Rect(4, 4, 2, 2), BG_MANUAL, "dark", manual_rois=[])
    assert np.isnan(bg.value) and bg.warnings


def test_roi_outside_image_is_clipped_and_flagged():
    img = np.full((10, 10), 5, np.uint8)
    m = measure_band(img, Rect(8, 8, 5, 5), "bright", 0.0)
    assert m.clipped and m.n_pixels == 4 and m.raw_sum == 20
    m = measure_band(img, Rect(20, 20, 5, 5), "bright", 0.0)
    assert m.n_pixels == 0 and np.isnan(m.corrected)


def test_saturation_counts():
    blot = make_blot([250], background=200)      # 200-250 → 0 にクリップ（黒つぶれ疑似例）
    m = measure_band(blot.data, blot.bands[0], "dark", 200.0)
    assert m.n_low_extreme == blot.bands[0].area
    assert m.corrected < blot.true_signal[0]     # 飽和すると真値を過小評価


def test_roi_size_effect():
    blot = make_blot([50], background=200, band_height=10)
    b = blot.bands[0]
    tight = measure_band(blot.data, b, "dark", 200.0).corrected
    loose = measure_band(blot.data, Rect(b.x, b.y - 5, b.w, b.h + 10), "dark", 200.0).corrected
    assert tight == pytest.approx(loose)   # 背景を正しく引けば余白は 0 を足すだけ
    raw_t = measure_band(blot.data, b, "dark", 255.0).corrected
    raw_l = measure_band(blot.data, Rect(b.x, b.y - 5, b.w, b.h + 10), "dark", 255.0).corrected
    assert raw_l > raw_t                   # 背景補正なしでは ROI を大きくするほど値が増える


def test_profile_and_peak_area():
    blot = make_blot([60], background=200, band_height=10, height=80)
    lane, band = blot.lanes[0], blot.bands[0]
    ys, vals = lane_profile(blot.data, lane)
    assert len(ys) == lane.h
    area, yy, base = profile_peak_area(ys, vals, band.y - 3, band.y2 + 3, "dark")
    expected = 60 * band.w / lane.w * band.h      # 行平均で見たバンドの深さ × 行数
    assert area == pytest.approx(expected)


def test_image_array_is_read_only(tmp_path):
    import tifffile
    from wbtool.core.imageio import load_image
    p = tmp_path / "a.tif"
    tifffile.imwrite(p, np.zeros((5, 5), np.uint16))
    img = load_image(p)
    with pytest.raises(ValueError):
        img.data[0, 0] = 1
