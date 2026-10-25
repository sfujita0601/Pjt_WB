import json

import numpy as np
import pandas as pd
import pytest

from wbtool.core.bandpanel import build_panel, composite_figure, crop_rect_for_image, display_array
from wbtool.core.figure import FigureStyle, StatAnnotation, error_half_width, figure_caption, format_p, plot_groups
from wbtool.core.imageio import sha256_file
from wbtool.core.pipeline import ImageCache
from tests.helpers import build_project


def test_error_bars():
    v = np.array([1.0, 2.0, 3.0, 4.0])
    sd = np.std(v, ddof=1)
    assert error_half_width(v, "sd") == pytest.approx(sd)
    assert error_half_width(v, "sem") == pytest.approx(sd / 2)
    from scipy import stats
    assert error_half_width(v, "ci95") == pytest.approx(stats.t.ppf(0.975, 3) * sd / 2)
    assert np.isnan(error_half_width(np.array([1.0]), "sd"))


def test_caption_mentions_error_bar_n_and_test():
    bio = pd.DataFrame({"group": ["C", "C", "T", "T", "T"], "fold": [1, 1.1, 2, 2.2, 1.9]})
    st = FigureStyle(error_bar="sem")
    cap = figure_caption(bio, st, stats_desc="Welch の t 検定。")
    assert "SEM" in cap and "C n = 2" in cap and "T n = 3" in cap and "Welch" in cap and "生物学的反復" in cap


def test_plot_with_annotations_and_log2(tmp_path):
    bio = pd.DataFrame({"group": ["C", "C", "T", "T"], "log2_fold": [0.1, -0.1, 1.0, 1.2]})
    st = FigureStyle(value_col="log2_fold", show_stats=True, error_bar="ci95")
    fig = plot_groups(bio, st, annotations=[StatAnnotation("C", "T", 0.0123)])
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert "p = 0.012" in texts
    fig.savefig(tmp_path / "a.svg")
    assert format_p(0.0004) == "p < 0.001"


def test_band_panel_crops_contiguous_region_and_keeps_raw_pixels(tmp_path):
    p, t, l = build_project(tmp_path, [1000, 1200, 2000], [5000, 6000, 5000], ["C", "C", "T"], ["C1", "C2", "T1"])
    cache = ImageCache()
    data = {im.image_id: cache.get(im.path).data for im in p.images}
    hashes = [sha256_file(im.path) for im in p.images]
    crop = crop_rect_for_image(p, "IMG001", margin=5)
    lanes = p.images[0].lanes
    assert crop.x == lanes[0].rect.x and crop.x2 == lanes[-1].rect.x2     # 左端〜右端の連続領域
    rows = build_panel(p, data, ["IMG001", "IMG002"], margin=5)
    assert len(rows) == 2
    r = rows[0]
    assert np.array_equal(r.pixels, data["IMG001"][crop.y:crop.y2, crop.x:crop.x2])   # 元の画素値のまま
    assert r.pixels.dtype == np.uint16
    disp = display_array(r)
    assert disp.min() == 0 and disp.max() == 1
    rec = r.record()
    assert rec["sha256"] == p.images[0].sha256 and rec["crop"] == crop.as_tuple()
    json.dumps(rec)
    bio = pd.DataFrame({"group": ["C", "C", "T"], "fold": [1, 1, 2]})
    fig, rows = composite_figure(p, data, ["IMG001", "IMG002"], bio, FigureStyle())
    fig.savefig(tmp_path / "c.pdf")
    assert [sha256_file(im.path) for im in p.images] == hashes        # 原画像は不変
