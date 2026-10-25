"""独立レビューで見つかった不具合の再発防止テスト。"""
import json

import numpy as np
import pandas as pd

from wbtool.core.export import settings_dict
from wbtool.core.normalize import normalize
from wbtool.core.pipeline import ImageCache, measure_project
from wbtool.core.stats import paired_test
from wbtool.core.stats_multi import multi_group_test
from wbtool.core.stats_runner import run_stats
from wbtool.core.validation import roi_specs
from wbtool.models.project import BG_MANUAL, Project, Rect
from tests.helpers import build_project


def test_old_project_paired_setting_is_migrated(tmp_path):
    p = Project()
    d = p.to_dict()
    d["settings"].pop("stat_design")
    d["settings"]["stat_test"] = "paired"
    d["settings"]["stat_value"] = "fold"
    q = Project.from_dict(d)
    assert q.settings.stat_design == "paired" and q.settings.stat_value == "ratio"


def test_settings_json_records_current_stats_settings():
    p = Project()
    p.settings.stat_goal, p.settings.stat_equal_var, p.settings.stat_groups = "all_pairs", True, ["A", "B", "C"]
    a = settings_dict(p)["analysis"]
    assert a["stat_goal"] == "all_pairs" and a["stat_equal_var"] is True and a["stat_groups_multi"] == ["A", "B", "C"]
    json.dumps(a)


def test_equal_var_zero_variance_refused():
    bio = pd.DataFrame({"group": list("AAABBBCCC"), "bio_rep": [f"r{i}" for i in range(9)],
                        "ratio": [1, 1, 1, 2, 2, 2, 3, 3, 3]})
    r = multi_group_test(bio, "ratio", ["A", "B", "C"], "all_pairs", equal_var=True)
    assert not r.ok and "分散が 0" in r.refusal


def test_nan_numerator_gets_status_and_counted_in_stats(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 1100, 1200, 2000, 2100, 2200], [5000] * 6,
                          ["C", "C", "C", "T", "T", "T"], ["C1", "C2", "C3", "T1", "T2", "T3"])
    im = p.images[0]
    im.bands[1].bg_method_override = BG_MANUAL      # 手動背景 ROI が無い → 背景 NaN → S NaN
    m = measure_project(p, ImageCache()).table
    p.settings.numerator_protein, p.settings.denominator_protein, p.settings.control_group = "Target", "Actin", "C"
    p.settings.stat_group_a, p.settings.stat_group_b = "C", "T"
    norm = normalize(m, "Target", "Actin", "C", p.samples)
    st = norm.sample_table
    assert "分子の S が無効" in st.loc[st.sample_id == "S2", "status"].iloc[0]
    out = run_stats(p.settings, norm)
    assert out.ok and out.two.n_missing == 1
    assert any("S2" in n for n in out.notes)


def test_paired_description_uses_number_of_pairs(tmp_path):
    from wbtool.core.normalize import NormalizationResult
    from wbtool.models.project import AnalysisSettings
    bio = pd.DataFrame({"group": ["A"] * 3 + ["B"] * 4, "bio_rep": ["a1", "a2", "a3", "b1", "b2", "b3", "b4"],
                        "subject_id": ["s1", "s2", "s3", "s1", "s2", "s3", "s4"],
                        "ratio": [1, 1.1, 0.9, 1.5, 1.8, 1.4, 2.0], "log2_ratio": np.nan})
    s = AnalysisSettings(stat_goal="two", stat_design="paired", stat_group_a="A", stat_group_b="B")
    out = run_stats(s, NormalizationResult(pd.DataFrame(), bio, 1.0))
    assert out.ok and "対の数 n = 3" in out.description


def test_paired_missing_counts_each_unused_subject():
    a = {"s1": 1.0, "s2": 1.1, "s3": 0.9, "s4": np.nan}
    b = {"s1": 1.5, "s2": 1.9, "s3": 1.2, "s5": 2.0}
    r = paired_test(a, b)
    assert r.n_pairs == 3 and r.n_missing == 2


def test_validation_skips_zero_area_background_roi(tmp_path):
    p, *_ = build_project(tmp_path, [1000], [5000], ["C"], ["C1"])
    im = p.images[0]
    im.bg_rois = [Rect(-50, -50, 10, 10), Rect(2, 2, 5, 5)]
    ids = [s["id"] for s in roi_specs(p, im.image_id)]
    assert "BG1:bg_manual" not in ids and "BG2:bg_manual" in ids
