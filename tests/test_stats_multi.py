"""3群以上の統計：信頼できる実装（statsmodels / pingouin / 手計算）との数値照合。

参照ライブラリは開発時のみ必要（pip install -e ".[dev]"）。無い環境ではスキップする。
"""
import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from wbtool.core.stats_multi import (
    dunnett_pairs, games_howell_pairs, multi_group_test, one_way_anova, tukey_pairs, welch_anova,
)

# 不等分散・不等 n のデータ（教材用の架空値）
DATA = {
    "Control": np.array([1.00, 1.12, 0.91, 0.97, 1.05]),
    "DrugA": np.array([1.35, 1.62, 1.18, 1.49]),
    "DrugB": np.array([2.10, 2.65, 1.71, 2.90, 2.33, 1.95]),
}
NAMES = list(DATA)
SAMPLES = list(DATA.values())


def long_df():
    return pd.DataFrame([(g, v) for g, a in DATA.items() for v in a], columns=["group", "value"])


def test_one_way_anova_matches_statsmodels():
    sm = pytest.importorskip("statsmodels.api")
    from statsmodels.formula.api import ols
    F, df1, df2, p, eta2 = one_way_anova(SAMPLES)
    table = sm.stats.anova_lm(ols("value ~ C(group)", data=long_df()).fit(), typ=1)
    assert F == pytest.approx(table["F"].iloc[0])
    assert p == pytest.approx(table["PR(>F)"].iloc[0])
    assert (df1, df2) == (table["df"].iloc[0], table["df"].iloc[1])
    assert eta2 == pytest.approx(table["sum_sq"].iloc[0] / table["sum_sq"].sum())


def test_welch_anova_matches_statsmodels_and_scipy():
    from statsmodels.stats.oneway import anova_oneway
    F, df1, df2, p = welch_anova(SAMPLES)
    ref = anova_oneway(SAMPLES, use_var="unequal", welch_correction=True)
    assert F == pytest.approx(ref.statistic)
    assert p == pytest.approx(ref.pvalue)
    assert df2 == pytest.approx(ref.df[1])
    sc = sps.f_oneway(*SAMPLES, equal_var=False)
    assert F == pytest.approx(sc.statistic) and p == pytest.approx(sc.pvalue)


def test_tukey_matches_statsmodels():
    from statsmodels.stats.multicomp import pairwise_tukeyhsd
    ours = tukey_pairs(NAMES, SAMPLES)
    d = long_df()
    ref = pairwise_tukeyhsd(d["value"], d["group"])
    rt = pd.DataFrame(ref.summary().data[1:], columns=ref.summary().data[0])
    for _, row in ours.iterrows():
        r = rt[(rt.group1 == row.group_a) & (rt.group2 == row.group_b)].iloc[0]
        assert row["diff"] == pytest.approx(float(r.meandiff), abs=1e-4)
        assert row["p_adj"] == pytest.approx(float(r["p-adj"]), abs=1e-3)   # statsmodels は表示を丸める
        assert row["ci_low"] == pytest.approx(float(r.lower), abs=1e-4)
        assert row["ci_high"] == pytest.approx(float(r.upper), abs=1e-4)


def test_games_howell_matches_pingouin():
    pg = pytest.importorskip("pingouin")
    ours = games_howell_pairs(NAMES, SAMPLES)
    ref = pg.pairwise_gameshowell(data=long_df(), dv="value", between="group")
    for _, row in ours.iterrows():
        r = ref[((ref.A == row.group_a) & (ref.B == row.group_b)) | ((ref.A == row.group_b) & (ref.B == row.group_a))].iloc[0]
        sign = 1 if r.A == row.group_a else -1
        # pingouin の diff は mean(A) − mean(B)
        assert row["diff"] == pytest.approx(-sign * r["diff"], rel=1e-9)
        assert row["p_adj"] == pytest.approx(r["pval"], rel=1e-6, abs=1e-9)
        assert row["df"] == pytest.approx(r["df"], rel=1e-9)
        assert abs(row["statistic"]) == pytest.approx(abs(r["T"]), rel=1e-9)


def test_dunnett_matches_known_values_and_scipy_docs_example():
    # SciPy ドキュメントの例（Dunnett 1955 の血球数データ）。SciPy の実装は R の multcomp と照合済み
    control = np.array([7.40, 8.50, 7.20, 8.24, 9.84, 8.32])
    drug_a = np.array([9.76, 8.80, 7.68, 9.36])
    drug_b = np.array([12.80, 9.68, 12.16, 9.20, 10.55])
    ours = dunnett_pairs("C", control, ["A", "B"], [drug_a, drug_b])
    # 数値積分の誤差（~1e-4）を許容
    assert ours["p_adj"].tolist() == pytest.approx([0.62004941, 0.0059035], abs=3e-4)
    # 同じ入力なら同じ出力（再現性）
    again = dunnett_pairs("C", control, ["A", "B"], [drug_a, drug_b])
    assert again["p_adj"].tolist() == ours["p_adj"].tolist()
    assert ours["diff"].tolist() == pytest.approx([drug_a.mean() - control.mean(), drug_b.mean() - control.mean()])
    assert (ours["ci_low"] < ours["diff"]).all() and (ours["diff"] < ours["ci_high"]).all()


def test_dunnett_family_wise_error_rate_simulation():
    """帰無仮説のもとで、少なくとも1つ有意になる割合が約 5% であること（多重性の調整の確認）。"""
    rng = np.random.default_rng(0)
    hits = 0
    n_sim = 400
    for _ in range(n_sim):
        c = rng.normal(0, 1, 5)
        others = [rng.normal(0, 1, 5) for _ in range(3)]
        p = dunnett_pairs("C", c, ["A", "B", "D"], others)["p_adj"].values
        hits += (p < 0.05).any()
    assert 0.02 < hits / n_sim < 0.09


def bio_table(design_subject=False):
    rows = []
    for g, arr in DATA.items():
        for i, v in enumerate(arr):
            rows.append({"group": g, "bio_rep": f"{g[0]}{i + 1}", "subject_id": f"P{i + 1}" if design_subject else "",
                         "ratio": v, "log2_ratio": np.log2(v)})
    return pd.DataFrame(rows)


def test_multi_group_wrapper_routes_methods():
    bio = bio_table()
    r = multi_group_test(bio, "ratio", NAMES, "vs_control", "Control", equal_var=True)
    assert r.ok and "Dunnett" in r.posthoc_name and len(r.pairs) == 2
    assert set(r.pairs["group_a"]) == {"Control"}
    r = multi_group_test(bio, "ratio", NAMES, "all_pairs", equal_var=True)
    assert r.ok and "Tukey" in r.posthoc_name and len(r.pairs) == 3
    r = multi_group_test(bio, "ratio", NAMES, "all_pairs", equal_var=False)
    assert r.ok and "Welch" in r.omnibus_name and "Games" in r.posthoc_name
    r = multi_group_test(bio, "ratio", NAMES, "vs_control", "Control", equal_var=False)
    assert r.ok and len(r.pairs) == 2 and set(r.pairs["group_a"]) == {"Control"}
    assert r.n == {"Control": 5, "DrugA": 4, "DrugB": 6}


def test_multi_group_refusals():
    bio = bio_table()
    assert not multi_group_test(bio, "ratio", NAMES, "all_pairs", design="paired").ok
    assert not multi_group_test(bio, "ratio", NAMES[:2], "all_pairs").ok
    assert not multi_group_test(bio, "ratio", NAMES, "vs_control", "Nope").ok
    shared = bio_table(design_subject=True)          # 同じ subject が複数群 → 反復測定
    r = multi_group_test(shared, "ratio", NAMES, "all_pairs")
    assert not r.ok and "subject_id" in r.refusal
    neg = bio.copy()
    neg.loc[0, "log2_ratio"] = np.nan                 # 比 ≤ 0 で log2 不可
    r = multi_group_test(neg, "log2_ratio", NAMES, "all_pairs")
    assert not r.ok and "擬似的" in r.refusal
    missing = bio.copy()
    missing.loc[0, "bio_rep"] = ""
    assert not multi_group_test(missing, "ratio", NAMES, "all_pairs").ok


def test_stats_runner_routes_and_builds_annotations(tmp_path):
    from wbtool.core.normalize import NormalizationResult
    from wbtool.core.stats_runner import run_stats
    from wbtool.models.project import AnalysisSettings
    bio = bio_table()
    norm = NormalizationResult(pd.DataFrame(), bio, 1.0)
    s = AnalysisSettings(control_group="Control", stat_goal="vs_control", stat_groups=NAMES, stat_value="log2_ratio")
    out = run_stats(s, norm)
    assert out.ok and out.kind == "multi" and len(out.annotations) == 2
    assert "調整済み" in out.description and "log2" in out.description
    assert set(out.table["value"]) == {"log2_ratio"}
    s2 = AnalysisSettings(stat_goal="two", stat_group_a="Control", stat_group_b="DrugA")
    out2 = run_stats(s2, norm)
    assert out2.ok and out2.kind == "two" and "Welch" in out2.method
    norm.blocking = ["混在"]
    assert not run_stats(s2, norm).ok


def test_stats_runner_uses_all_groups_when_none_selected():
    from wbtool.core.normalize import NormalizationResult
    from wbtool.core.stats_runner import run_stats
    from wbtool.models.project import AnalysisSettings
    norm = NormalizationResult(pd.DataFrame(), bio_table(), 1.0)
    out = run_stats(AnalysisSettings(control_group="Control", stat_goal="all_pairs", stat_groups=[]), norm)
    assert out.ok and len(out.table) == 3
