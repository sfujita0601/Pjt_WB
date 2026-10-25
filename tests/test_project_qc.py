import json

import numpy as np
import pandas as pd

from wbtool.core.export import export_all, samples_from_csv, samples_to_csv
from wbtool.core.imageio import sha256_file
from wbtool.core.lanes import suggest_lanes
from wbtool.core.pipeline import ImageCache, compare_with_saved, measure_project, results_to_records
from wbtool.core.qc import run_qc
from wbtool.core.synthetic import make_blot
from wbtool.models.project import Project, Rect
from tests.helpers import build_project


def test_save_reload_roundtrip_and_hash(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 2000], [5000, 5000], ["C", "T"], ["R1", "R1"])
    hashes = [sha256_file(im.path) for im in p.images]
    m = measure_project(p, ImageCache()).table
    p.last_results = results_to_records(m)
    p.log("test", "保存")
    path = tmp_path / "proj.wbproj.json"
    p.save(path)
    q = Project.load(path)
    assert q.to_dict()["images"] == p.to_dict()["images"]
    assert [s.sample_id for s in q.samples] == ["S1", "S2"]
    m2 = measure_project(q, ImageCache()).table
    assert compare_with_saved(m2, q.last_results) == []
    assert [sha256_file(im.path) for im in q.images] == hashes    # 原画像は不変


def test_reload_finds_moved_folder_by_relative_path(tmp_path):
    proj_dir = tmp_path / "a"
    proj_dir.mkdir()
    p, *_ = build_project(proj_dir, [1000], [5000], ["C"], ["R1"])
    p.save(proj_dir / "p.json")
    moved = tmp_path / "b"
    (proj_dir).rename(moved)
    q = Project.load(moved / "p.json")
    assert all(str(moved) in im.path for im in q.images)


def test_compare_detects_change(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 2000], [5000, 5000], ["C", "T"], ["R1", "R1"])
    m = measure_project(p, ImageCache()).table
    saved = results_to_records(m)
    p.images[0].bands[0].rect = Rect(0, 0, 5, 5)
    m2 = measure_project(p, ImageCache()).table
    assert compare_with_saved(m2, saved)


def test_qc_detects_problems(tmp_path):
    p, t, l = build_project(tmp_path, [1000, 2000], [5000, 5000], ["C", "T"], ["R1", ""])
    im = p.images[0]
    im.bands[1].rect = Rect(im.bands[0].rect.x, im.bands[0].rect.y, im.bands[0].rect.w + 30, 12)  # 重複＋サイズ違い
    im.lanes[1].sample_id = "S1"                                                                    # 重複割当
    p.images[1].bands[0].rect = Rect(-3, 10, 10, 10)                                               # 画像外
    m = measure_project(p, ImageCache()).table
    data = {i.image_id: ImageCache().get(i.path).data for i in p.images}
    cats = {q.category for q in run_qc(p, m, data)}
    for c in ("ROI サイズ不一致", "ROI 重複", "重複割当", "ROI", "試料情報"):
        assert c in cats, c


def test_qc_saturation_and_bg_cross(tmp_path):
    p, *_ = build_project(tmp_path, [50000, 1000], [5000, 5000], ["C", "T"], ["R1", "R2"])
    p.images[0].bg_gap = 0
    p.images[0].bg_height = 200
    m = measure_project(p, ImageCache()).table
    data = {i.image_id: ImageCache().get(i.path).data for i in p.images}
    cats = {q.category for q in run_qc(p, m, data)}
    assert "飽和候補（バンド内）" in cats
    assert "飽和・黒つぶれ候補" in cats


def test_qc_multiple_exposures_included(tmp_path):
    p, *_ = build_project(tmp_path, [1000], [5000], ["C"], ["R1"])
    p.images[1].protein_target = "Target"   # 同じ膜・同じタンパク質の画像が2枚とも定量対象
    cats = {q.category for q in run_qc(p, pd.DataFrame(), {})}
    assert "露光の重複" in cats


def test_lane_suggestion_finds_lanes():
    blot = make_blot([60, 80, 100, 120, 90], background=200, height=80, blur_sigma=1)
    s = suggest_lanes(blot.data, 5, "dark")
    assert s.method == "peaks"
    for got, true in zip(s.rects, blot.lanes):
        assert abs((got.x + got.w / 2) - (true.x + true.w / 2)) <= 3
    flat = np.full((50, 100), 200, np.uint8)
    s = suggest_lanes(flat, 4, "dark")
    assert s.method == "equal_spacing" and len(s.rects) == 4


def test_csv_samples_roundtrip_and_export(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 2000], [5000, 5000], ["C", "T"], ["R1", "R1"])
    samples_to_csv(p, tmp_path / "s.csv")
    q = Project()
    q.images = p.images
    for im in q.images:
        for l in im.lanes:
            l.sample_id = ""
    msgs = samples_from_csv(q, tmp_path / "s.csv")
    assert "新規 2 件" in msgs[0]
    assert q.images[0].lanes[0].sample_id == "S1"
    m = measure_project(p, ImageCache()).table
    data = {i.image_id: ImageCache().get(i.path).data for i in p.images}
    files = export_all(p, m, None, None, data, tmp_path / "out")
    names = {f.split("/")[-1] for f in files}
    assert {"measurements.csv", "roi_list.csv", "settings.json"} <= names
    js = json.loads((tmp_path / "out" / "settings.json").read_text(encoding="utf-8"))
    assert js["images"][0]["sha256"] == p.images[0].sha256


def test_example_project_reproduces_truth(tmp_path):
    from wbtool.core.examples import build_example_project
    from wbtool.core.normalize import normalize
    from wbtool.core.synthetic import example_dataset
    p = build_example_project(tmp_path)
    m = measure_project(p, ImageCache()).table
    res = normalize(m, "pERK", "Actin", "Control", p.samples)
    ds = example_dataset()
    true_fold = ds["true_ratio"] / ds["true_ratio"][:4].mean()
    got = res.bio_table.sort_values(["group", "bio_rep"])["fold"].values
    # ノイズ・背景傾斜・ぼかしがあっても真の fold に近いこと（教材として妥当か）
    assert np.allclose(got, true_fold, rtol=0.08)
