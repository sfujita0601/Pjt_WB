"""Fiji 照合ツールのテスト。

Fiji 本体はこの環境に無いため、Fiji の Results CSV と同じ形式・同じ定義の値を NumPy で作って検証する
（実際の Fiji との照合は docs/validation_imagej.md の手順で、実画像を用いて行う：未検証）。
"""
import numpy as np
import pandas as pd

from wbtool.core.pipeline import ImageCache, measure_project
from wbtool.core.validation import (
    ID_COL, compare_normalization, compare_with_fiji, imagej_macro, read_fiji_results, report_markdown, roi_specs,
)
from wbtool.models.project import Rect
from tests.helpers import build_project


def fake_fiji_csv(project, image_id, data, path, transform=None, shift=None):
    """Fiji の Measure と同じ定義（Area, Mean, IntDen, RawIntDen）で Results CSV を作る。"""
    rows = []
    for i, s in enumerate(roi_specs(project, image_id), 1):
        r = s["rect"]
        if shift and s["id"] == shift[0]:
            r = Rect(r.x + shift[1], r.y, r.w + shift[2], r.h)
        px = data[r.y:r.y2, r.x:r.x2].astype(float)
        if transform:
            px = transform(px)
        rows.append({" ": i, "Area": px.size, "Mean": px.mean(), "Min": px.min(), "Max": px.max(),
                     "IntDen": px.sum(), "RawIntDen": px.sum(), ID_COL: s["id"]})
    pd.DataFrame(rows).to_csv(path, index=False)
    return read_fiji_results(path)


def setup(tmp_path):
    p, t, l = build_project(tmp_path, [1000, 1200, 2000, 2600], [5000, 6000, 5000, 6500],
                            ["C", "C", "T", "T"], ["C1", "C2", "T1", "T2"])
    p.settings.numerator_protein, p.settings.denominator_protein, p.settings.control_group = "Target", "Actin", "C"
    cache = ImageCache()
    return p, cache, measure_project(p, cache).table


def test_macro_contains_identical_rois(tmp_path):
    p, cache, m = setup(tmp_path)
    mac = imagej_macro(p, "IMG001")
    b = p.images[0].bands[0].rect
    assert f"makeRectangle({b.x}, {b.y}, {b.w}, {b.h});" in mac
    assert "B1:bg_above" in mac and "B1:bg_below" in mac
    assert 'distance=0 known=0' in mac and p.images[0].sha256 in mac


def test_exact_match_band_and_normalization(tmp_path):
    p, cache, m = setup(tmp_path)
    reps = []
    for im in p.images:
        data = cache.get(im.path).data
        fj = fake_fiji_csv(p, im.image_id, data, tmp_path / f"{im.image_id}.csv")
        rep = compare_with_fiji(p, im.image_id, data, fj, m)
        assert rep.all_match, rep.roi_table[~rep.roi_table.match]
        reps.append(rep)
    norm = compare_normalization(p, m, reps)
    assert norm["match"].all()
    md = report_markdown(p, reps, norm)
    assert "すべて一致" in md


def test_diagnoses_inversion_8bit_and_roi_shift(tmp_path):
    p, cache, m = setup(tmp_path)
    im = p.images[0]
    data = cache.get(im.path).data
    inv = fake_fiji_csv(p, "IMG001", data, tmp_path / "inv.csv", transform=lambda x: 65535 - x)
    rep = compare_with_fiji(p, "IMG001", data, inv, m)
    assert not rep.all_match and "極性" in rep.roi_table["diagnosis"].iloc[0]
    b8 = fake_fiji_csv(p, "IMG001", data, tmp_path / "b8.csv", transform=lambda x: np.floor(x / 257))
    rep = compare_with_fiji(p, "IMG001", data, b8, m)
    assert "8-bit" in rep.roi_table["diagnosis"].iloc[0]
    sh = fake_fiji_csv(p, "IMG001", data, tmp_path / "sh.csv", shift=("B2:band", 2, 1))
    rep = compare_with_fiji(p, "IMG001", data, sh, m)
    row = rep.roi_table[rep.roi_table.wbtool_id == "B2:band"].iloc[0]
    assert not row.match and "ROI の位置" in row.diagnosis
    brow = rep.band_table[rep.band_table.band_id == "B2"].iloc[0]
    assert not brow.match and "B2:band" in brow.diagnosis


def test_missing_rows_reported(tmp_path):
    p, cache, m = setup(tmp_path)
    data = cache.get(p.images[0].path).data
    fj = fake_fiji_csv(p, "IMG001", data, tmp_path / "a.csv")
    fj = fj[fj[ID_COL] != "B3:band"]
    rep = compare_with_fiji(p, "IMG001", data, fj, m)
    assert "ありません" in rep.roi_table[rep.roi_table.wbtool_id == "B3:band"]["diagnosis"].iloc[0]
