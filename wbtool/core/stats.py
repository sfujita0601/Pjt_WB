"""統計解析（MVP：記述統計、Welch の t 検定、対応のある t 検定）。GUI 非依存。

解析単位は biological replicate（normalize.bio_table の1行）。technical replicate は
集約済みのため、ここで独立な n として数えられることはない。
3群以上の ANOVA・多重比較・混合効果モデルは Phase 2（未実装）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as sps


@dataclass
class Descriptive:
    group: str
    n: int
    mean: float
    sd: float
    sem: float
    ci_low: float
    ci_high: float
    median: float
    minimum: float
    maximum: float


def describe(values, group: str = "") -> Descriptive:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = x.size
    nan = float("nan")
    if n == 0:
        return Descriptive(group, 0, nan, nan, nan, nan, nan, nan, nan, nan)
    mean = float(x.mean())
    sd = float(x.std(ddof=1)) if n >= 2 else nan
    sem = sd / np.sqrt(n) if n >= 2 else nan
    if n >= 2:
        tcrit = sps.t.ppf(0.975, n - 1)
        lo, hi = mean - tcrit * sem, mean + tcrit * sem
    else:
        lo = hi = nan
    return Descriptive(group, n, mean, sd, sem, float(lo), float(hi), float(np.median(x)), float(x.min()), float(x.max()))


@dataclass
class TestResult:
    test_name: str
    ok: bool
    group_a: str
    group_b: str
    n_a: int = 0
    n_b: int = 0
    n_pairs: int = 0
    statistic: float = float("nan")
    df: float = float("nan")
    p_value: float = float("nan")
    mean_diff: float = float("nan")       # B − A
    ci_low: float = float("nan")
    ci_high: float = float("nan")
    effect_name: str = ""
    effect_size: float = float("nan")
    n_missing: int = 0
    refusal: str = ""
    notes: list[str] = field(default_factory=list)


ASSUMPTIONS = {
    "welch": [
        "2群の biological replicate が互いに独立であること（同じ個体・同じ日の試料が両群にまたがる場合は対応のある検定を検討）。",
        "各群の値がおおむね正規分布に従うこと（n が小さいと確認は困難。点をすべて図示して外れ値を確認）。",
        "等分散は仮定しない（Welch の補正）。",
        "比（ratio/fold）は右に裾を引く分布になりやすい。対数変換の要否は事前に決めておく（本ツールは変換しない）。",
    ],
    "paired": [
        "同じ bio_rep ID を持つ2群の値が対応していること（同じ個体・同じ実験日など）。",
        "対ごとの差がおおむね正規分布に従うこと。",
        "対が欠けている bio_rep は解析から除外される。",
    ],
}


def welch_test(a, b, group_a: str = "A", group_b: str = "B") -> TestResult:
    x = np.asarray(a, float); x = x[np.isfinite(x)]
    y = np.asarray(b, float); y = y[np.isfinite(y)]
    r = TestResult("Welch の t 検定（独立2群・等分散を仮定しない）", False, group_a, group_b, x.size, y.size)
    if x.size < 2 or y.size < 2:
        r.refusal = "各群に2つ以上の biological replicate が必要です。"
        return r
    va, vb = x.var(ddof=1), y.var(ddof=1)
    if va == 0 and vb == 0:
        r.refusal = "両群とも分散が 0 のため検定できません。"
        return r
    res = sps.ttest_ind(y, x, equal_var=False)
    se = np.sqrt(va / x.size + vb / y.size)
    df = (va / x.size + vb / y.size) ** 2 / ((va / x.size) ** 2 / (x.size - 1) + (vb / y.size) ** 2 / (y.size - 1))
    diff = float(y.mean() - x.mean())
    tcrit = sps.t.ppf(0.975, df)
    sp = np.sqrt(((x.size - 1) * va + (y.size - 1) * vb) / (x.size + y.size - 2))
    d = diff / sp if sp > 0 else float("nan")
    j = 1 - 3 / (4 * (x.size + y.size) - 9)   # Hedges の小標本補正
    r.ok = True
    r.statistic, r.df, r.p_value = float(res.statistic), float(df), float(res.pvalue)
    r.mean_diff, r.ci_low, r.ci_high = diff, diff - tcrit * se, diff + tcrit * se
    r.effect_name, r.effect_size = "Hedges の g（プール SD 基準、小標本補正あり）", float(d * j)
    if min(x.size, y.size) < 3:
        r.notes.append("n < 3 の群があります。検定結果はきわめて不安定です。")
    return r


def paired_test(a: dict, b: dict, group_a: str = "A", group_b: str = "B") -> TestResult:
    """a, b は {bio_rep: 値}。共通の bio_rep のみを対として用いる。"""
    keys = [k for k in a if k in b and np.isfinite(a[k]) and np.isfinite(b[k])]
    missing = len(set(a) ^ set(b)) + sum(1 for k in a if k in b and k not in keys)
    r = TestResult("対応のある t 検定", False, group_a, group_b, len(a), len(b), len(keys), n_missing=missing)
    if len(keys) < 2:
        r.refusal = "対になった biological replicate が2組以上必要です（両群で同じ bio_rep ID を使ってください）。"
        return r
    d = np.array([b[k] - a[k] for k in keys], float)
    sd = d.std(ddof=1)
    if sd == 0:
        r.refusal = "対の差の分散が 0 のため検定できません。"
        return r
    n = d.size
    res = sps.ttest_rel([b[k] for k in keys], [a[k] for k in keys])
    tcrit = sps.t.ppf(0.975, n - 1)
    se = sd / np.sqrt(n)
    r.ok = True
    r.statistic, r.df, r.p_value = float(res.statistic), float(n - 1), float(res.pvalue)
    r.mean_diff, r.ci_low, r.ci_high = float(d.mean()), float(d.mean() - tcrit * se), float(d.mean() + tcrit * se)
    r.effect_name, r.effect_size = "d_z（差の平均 ÷ 差の SD）", float(d.mean() / sd)
    if missing:
        r.notes.append(f"対が揃わない bio_rep が {missing} 個あり、除外しました。")
    if n < 3:
        r.notes.append("対の数が 3 未満です。検定結果はきわめて不安定です。")
    return r


def two_group_test(bio: pd.DataFrame, value_col: str, group_a: str, group_b: str, test: str) -> TestResult:
    """bio_table（biological replicate 単位）から2群比較を行う。独立単位が不明なら計算しない。"""
    name = {"welch": "Welch の t 検定", "paired": "対応のある t 検定"}.get(test, test)
    if test not in ("welch", "paired"):
        return TestResult(name, False, group_a, group_b, refusal="この検定は未実装です（Phase 2）。")
    if not group_a or not group_b or group_a == group_b:
        return TestResult(name, False, group_a, group_b, refusal="異なる2群を選択してください。")
    sub = bio[bio["group"].isin([group_a, group_b])]
    if sub.empty:
        return TestResult(name, False, group_a, group_b, refusal="選択した群にデータがありません。")
    if (sub["bio_rep"].astype(str) == "").any():
        return TestResult(name, False, group_a, group_b,
                          refusal="bio_rep が未入力の行があり、独立な単位が不明です。試料表を修正してください。")
    if sub.duplicated(["group", "bio_rep"]).any():
        return TestResult(name, False, group_a, group_b, refusal="同じ群に同じ bio_rep が重複しています。")
    a = sub[sub["group"] == group_a].set_index("bio_rep")[value_col]
    b = sub[sub["group"] == group_b].set_index("bio_rep")[value_col]
    n_missing = int(a.isna().sum() + b.isna().sum())
    if test == "welch":
        r = welch_test(a.values, b.values, group_a, group_b)
        shared = set(a.index) & set(b.index)
        if shared:
            r.notes.append(
                f"両群に同じ bio_rep ID（{', '.join(sorted(map(str, shared)))}）があります。"
                "同じ個体・同じ実験日の対応がある設計なら「対応のある t 検定」が適切です。"
            )
        r.n_missing += n_missing
    else:
        # 欠損値の bio_rep は対が揃わないものとして paired_test 側で1回だけ数える
        r = paired_test(a.dropna().to_dict(), b.dropna().to_dict(), group_a, group_b)
    r.notes.extend(ASSUMPTIONS[test])
    if value_col == "fold":
        r.notes.append("fold は全値を同じ定数（Control 平均）で割った値なので、比（ratio）で検定しても p 値は同じです。")
    return r
