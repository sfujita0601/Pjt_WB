import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from wbtool.core.normalize import normalize
from wbtool.core.pipeline import ImageCache, measure_project
from wbtool.core.stats import describe, paired_test, two_group_test, welch_test
from tests.helpers import build_project


def test_ratio_and_fold_reproduce_known_values(tmp_path):
    tgt = [1000, 1200, 2000, 2600]
    lc = [5000, 6000, 5000, 6500]
    p, t, l = build_project(tmp_path, tgt, lc, ["C", "C", "T", "T"], ["R1", "R2", "R1", "R2"])
    m = measure_project(p, ImageCache()).table
    res = normalize(m, "Target", "Actin", "C", p.samples)
    ratios = np.array(tgt) / np.array(lc)            # バンド面積は同じなので振幅比＝積分比
    assert np.allclose(res.sample_table["ratio"].values, ratios)
    ctrl = ratios[:2].mean()
    assert res.control_mean == pytest.approx(ctrl)
    assert np.allclose(res.bio_table["fold"].values, ratios / ctrl)
    assert res.bio_table[res.bio_table.group == "C"]["fold"].mean() == pytest.approx(1.0)


def test_technical_replicates_not_counted_as_n(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 1100, 2000, 2200, 2100], [5000] * 5,
                          ["C", "C", "T", "T", "T"], ["R1", "R1", "R1", "R1", "R2"],
                          ["T1", "T2", "T1", "T2", "T1"])
    m = measure_project(p, ImageCache()).table
    res = normalize(m, "Target", "Actin", "C", p.samples)
    bio = res.bio_table
    assert len(bio[bio.group == "C"]) == 1            # 2 technical → 1 biological
    assert len(bio[bio.group == "T"]) == 2
    assert bio[(bio.group == "C")]["n_tech"].iloc[0] == 2
    assert bio[(bio.group == "C")]["ratio"].iloc[0] == pytest.approx((0.2 + 0.22) / 2)
    r = two_group_test(bio, "ratio", "C", "T", "welch")
    assert not r.ok and r.n_a == 1                    # n=1 なので検定しない


def test_zero_or_missing_denominator_no_division(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 1000], [5000, 0], ["C", "T"], ["R1", "R1"])
    # 2 番目の LC バンドを背景だけの場所に置いても分母 ≈ 0 → 計算しない
    m = measure_project(p, ImageCache()).table
    res = normalize(m, "Target", "Actin", "C", p.samples)
    st = res.sample_table
    assert np.isnan(st.loc[st.sample_id == "S2", "ratio"]).all()
    assert "分母" in st.loc[st.sample_id == "S2", "status"].iloc[0]
    # 対応する LC バンドが無い（誤った対応付け）
    p.images[1].lanes[0].sample_id = "S9"
    m = measure_project(p, ImageCache()).table
    res = normalize(m, "Target", "Actin", "C", p.samples)
    st = res.sample_table
    assert "Actin のバンドがありません" in st.loc[st.sample_id == "S1", "status"].iloc[0]


def test_duplicate_assignment_blocks_ratio(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 1000], [5000, 5000], ["C", "T"], ["R1", "R1"])
    p.images[0].lanes[1].sample_id = "S1"
    m = measure_project(p, ImageCache()).table
    res = normalize(m, "Target", "Actin", "C", p.samples)
    st = res.sample_table
    assert "重複割当" in st.loc[st.sample_id == "S1", "status"].iloc[0]
    assert np.isnan(st.loc[st.sample_id == "S1", "ratio"].iloc[0])


def test_excluded_band_and_excluded_image(tmp_path):
    p, *_ = build_project(tmp_path, [1000, 1000], [5000, 5000], ["C", "T"], ["R1", "R1"])
    p.images[0].bands[0].excluded = True
    m = measure_project(p, ImageCache()).table
    st = normalize(m, "Target", "Actin", "C", p.samples).sample_table
    assert np.isnan(st.loc[st.sample_id == "S1", "ratio"].iloc[0])
    p.images[0].bands[0].excluded = False
    p.images[1].include = False
    m = measure_project(p, ImageCache()).table
    st = normalize(m, "Target", "Actin", "C", p.samples).sample_table
    assert st["ratio"].isna().all()


def test_describe():
    d = describe([1, 2, 3, 4])
    assert d.n == 4 and d.mean == 2.5 and d.sd == pytest.approx(np.std([1, 2, 3, 4], ddof=1))
    t = sps.t.ppf(0.975, 3)
    assert d.ci_low == pytest.approx(2.5 - t * d.sd / 2)


def test_welch_matches_scipy_and_manual():
    a, b = [1.0, 1.2, 0.9, 1.1], [1.8, 2.3, 2.0, 2.6, 1.9]
    r = welch_test(a, b)
    ref = sps.ttest_ind(b, a, equal_var=False)
    assert r.ok and r.p_value == pytest.approx(ref.pvalue) and r.statistic == pytest.approx(ref.statistic)
    assert r.mean_diff == pytest.approx(np.mean(b) - np.mean(a))
    assert r.ci_low < r.mean_diff < r.ci_high


def test_paired():
    a = {"R1": 1.0, "R2": 1.1, "R3": 0.9, "R4": 1.0}
    b = {"R1": 1.5, "R2": 1.9, "R3": 1.2, "R5": 3.0}
    r = paired_test(a, b)
    ref = sps.ttest_rel([1.5, 1.9, 1.2], [1.0, 1.1, 0.9])
    assert r.ok and r.n_pairs == 3 and r.p_value == pytest.approx(ref.pvalue)
    assert r.n_missing == 2


def test_refuse_when_bio_rep_missing():
    bio = pd.DataFrame({"group": ["C", "C", "T", "T"], "bio_rep": ["R1", "", "R1", "R2"], "ratio": [1, 1, 2, 2]})
    r = two_group_test(bio, "ratio", "C", "T", "welch")
    assert not r.ok and "bio_rep" in r.refusal


def test_unimplemented_test_refused():
    bio = pd.DataFrame({"group": ["C", "T"], "bio_rep": ["R1", "R1"], "ratio": [1, 2]})
    assert not two_group_test(bio, "ratio", "C", "T", "anova").ok


def test_paired_missing_counted_once():
    bio = pd.DataFrame({"group": ["C", "C", "C", "T", "T", "T"], "bio_rep": ["R1", "R2", "R3"] * 2,
                        "ratio": [1.0, 1.1, 0.9, 1.5, np.nan, 1.3]})
    r = two_group_test(bio, "ratio", "C", "T", "paired")
    assert r.n_pairs == 2 and r.n_missing == 1
