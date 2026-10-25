"""3群以上の比較（独立群のみ）。GUI 非依存。

| 目的 | 等分散を仮定 | 等分散を仮定しない（既定） |
|---|---|---|
| 対照群との比較 | 一元配置 ANOVA ＋ Dunnett | Welch ANOVA ＋ Games–Howell（対照群との対のみ表示） |
| 全群間の比較 | 一元配置 ANOVA ＋ Tukey（Tukey–Kramer） | Welch ANOVA ＋ Games–Howell |

- 解析単位は生物学的反復（normalize.bio_table の1行）。
- 対応のある3群以上（反復測定）は独立群の ANOVA に流し込まず、検定しない（未実装）。
- 数値は SciPy（f_oneway, tukey_hsd, dunnett, studentized_range）で計算し、
  tests/test_stats_multi.py で statsmodels / pingouin と照合している。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats as sps

PAIR_COLS = ["group_a", "group_b", "diff", "ci_low", "ci_high", "statistic", "df", "p_adj", "method"]

ASSUMPTIONS_MULTI = {
    "anova": [
        "各群の生物学的反復が互いに独立であること（同じ個体・同じ試料が複数群にまたがる反復測定デザインには使えない）。",
        "各群の値がおおむね正規分布に従うこと（n が小さいと確認は困難。個々の点を図示して確認）。",
        "各群の分散が等しいこと（等分散を仮定しない場合は Welch ANOVA を選ぶ）。",
    ],
    "welch": [
        "各群の生物学的反復が互いに独立であること。",
        "各群の値がおおむね正規分布に従うこと。",
        "等分散は仮定しない（Welch ANOVA、Games–Howell）。各群 n が小さいと検出力が低い。",
    ],
}


@dataclass
class MultiResult:
    ok: bool
    title: str = ""
    groups: list[str] = field(default_factory=list)
    n: dict[str, int] = field(default_factory=dict)
    omnibus_name: str = ""
    F: float = float("nan")
    df1: float = float("nan")
    df2: float = float("nan")
    p_omnibus: float = float("nan")
    eta_squared: float = float("nan")
    posthoc_name: str = ""
    pairs: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=PAIR_COLS))
    n_missing: int = 0
    refusal: str = ""
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 個別の検定
def one_way_anova(samples: list[np.ndarray]) -> tuple[float, float, float, float, float]:
    """F, df1, df2, p, η² を返す（古典的一元配置 ANOVA）。"""
    res = sps.f_oneway(*samples)
    k = len(samples)
    n = sum(len(s) for s in samples)
    allv = np.concatenate(samples)
    ss_between = sum(len(s) * (s.mean() - allv.mean()) ** 2 for s in samples)
    ss_total = ((allv - allv.mean()) ** 2).sum()
    eta2 = ss_between / ss_total if ss_total > 0 else float("nan")
    return float(res.statistic), float(k - 1), float(n - k), float(res.pvalue), float(eta2)


def welch_anova(samples: list[np.ndarray]) -> tuple[float, float, float, float]:
    """Welch の一元配置 ANOVA。F, df1, df2（Welch 補正）, p を返す。"""
    k = len(samples)
    n = np.array([len(s) for s in samples], float)
    m = np.array([s.mean() for s in samples])
    v = np.array([s.var(ddof=1) for s in samples])
    w = n / v
    mw = (w * m).sum() / w.sum()
    a = (w * (m - mw) ** 2).sum() / (k - 1)
    tmp = (((1 - w / w.sum()) ** 2) / (n - 1)).sum()
    b = 1 + 2 * (k - 2) / (k ** 2 - 1) * tmp
    F = a / b
    df2 = (k ** 2 - 1) / (3 * tmp)
    p = sps.f.sf(F, k - 1, df2)
    return float(F), float(k - 1), float(df2), float(p)


def tukey_pairs(names: list[str], samples: list[np.ndarray]) -> pd.DataFrame:
    res = sps.tukey_hsd(*samples)
    ci = res.confidence_interval(0.95)
    rows = []
    for i, j in combinations(range(len(names)), 2):
        # diff = 群 b の平均 − 群 a の平均
        rows.append({"group_a": names[i], "group_b": names[j], "diff": float(-res.statistic[i, j]),
                     "ci_low": float(-ci.high[i, j]), "ci_high": float(-ci.low[i, j]), "statistic": np.nan,
                     "df": float(sum(len(s) for s in samples) - len(samples)), "p_adj": float(res.pvalue[i, j]),
                     "method": "Tukey（Tukey–Kramer）"})
    return pd.DataFrame(rows, columns=PAIR_COLS)


def dunnett_pairs(control: str, ctrl: np.ndarray, names: list[str], samples: list[np.ndarray]) -> pd.DataFrame:
    # p 値は多変量 t 分布の数値積分（準モンテカルロ、誤差 ~1e-4）。同じ入力で同じ出力になるよう乱数を固定する
    res = sps.dunnett(*samples, control=ctrl, alternative="two-sided", rng=np.random.default_rng(20261025))
    ci = res.confidence_interval(0.95)
    df = float(sum(len(s) for s in samples) + len(ctrl) - len(samples) - 1)
    rows = []
    for i, name in enumerate(names):
        rows.append({"group_a": control, "group_b": name, "diff": float(samples[i].mean() - ctrl.mean()),
                     "ci_low": float(ci.low[i]), "ci_high": float(ci.high[i]), "statistic": float(res.statistic[i]),
                     "df": df, "p_adj": float(res.pvalue[i]), "method": "Dunnett（対照群との比較）"})
    return pd.DataFrame(rows, columns=PAIR_COLS)


def games_howell_pairs(names: list[str], samples: list[np.ndarray], only_with: str | None = None) -> pd.DataFrame:
    """Games–Howell の多重比較。only_with を指定すると、その群を含む対だけを返す（族の大きさは全対のまま＝保守的）。"""
    k = len(samples)
    rows = []
    for i, j in combinations(range(k), 2):
        if only_with is not None and only_with not in (names[i], names[j]):
            continue
        a, b = samples[i], samples[j]
        if only_with is not None and names[j] == only_with:   # 対照群を group_a に揃える
            i, j, a, b = j, i, b, a
        va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
        se = np.sqrt(va + vb)
        diff = b.mean() - a.mean()
        df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
        t = diff / se
        p = sps.studentized_range.sf(abs(t) * np.sqrt(2), k, df)
        qcrit = sps.studentized_range.ppf(0.95, k, df)
        half = qcrit / np.sqrt(2) * se
        rows.append({"group_a": names[i], "group_b": names[j], "diff": float(diff), "ci_low": float(diff - half),
                     "ci_high": float(diff + half), "statistic": float(t), "df": float(df), "p_adj": float(min(p, 1.0)),
                     "method": "Games–Howell"})
    return pd.DataFrame(rows, columns=PAIR_COLS)


# ------------------------------------------------------------------ まとめ役
def multi_group_test(bio: pd.DataFrame, value_col: str, groups: list[str], goal: str, control: str = "",
                     equal_var: bool = False, design: str = "independent") -> MultiResult:
    r = MultiResult(False, groups=list(groups))
    if design != "independent":
        r.refusal = ("対応のある3群以上のデザイン（同じ個体・同じ試料を複数条件で測る反復測定）は、"
                     "独立群の ANOVA では解析できません。反復測定 ANOVA／混合効果モデルは未実装のため、検定しません。")
        return r
    if len(groups) < 3:
        r.refusal = "3群以上を選んでください（2群なら「2群の比較」を使います）。"
        return r
    if goal == "vs_control" and control not in groups:
        r.refusal = "対照群との比較には、比較に含める群の中に対照群が必要です。［正規化］で対照群を選んでください。"
        return r
    sub = bio[bio["group"].isin(groups)]
    if (sub["bio_rep"].astype(str) == "").any():
        r.refusal = "生物学的反復（bio_rep）が未入力の行があり、独立な単位が不明です。試料表を修正してください。"
        return r
    if sub.duplicated(["group", "bio_rep"]).any():
        r.refusal = "同じ群に同じ生物学的反復が重複しています。"
        return r
    shared = sub[sub["subject_id"].astype(str) != ""] if "subject_id" in sub else sub.iloc[0:0]
    if len(shared) and shared.groupby("subject_id")["group"].nunique().max() > 1:
        r.refusal = ("同じ subject_id が複数の群にあります（同じ個体・試料を複数条件で測ったデザイン）。"
                     "独立群の ANOVA は使えません。反復測定の解析は未実装です。")
        return r
    samples, n_missing = [], 0
    for g in groups:
        vals = sub.loc[sub["group"] == g, value_col].astype(float)
        n_missing += int(vals.isna().sum())
        samples.append(vals.dropna().values)
    r.n = {g: len(s) for g, s in zip(groups, samples)}
    r.n_missing = n_missing
    if value_col.startswith("log2") and n_missing:
        r.refusal = ("log2 変換できない値（比が 0 以下または欠損）があります。0 や負の値を擬似的な正の値に置き換えることはしません。"
                     "比（線形スケール）で解析するか、該当バンドの測定を見直してください。")
        return r
    small = [g for g, s in zip(groups, samples) if len(s) < 2]
    if small:
        r.refusal = f"生物学的反復が2未満の群があります：{', '.join(small)}。各群に2以上（できれば3以上）必要です。"
        return r
    if not equal_var and any(s.var(ddof=1) == 0 for s in samples):
        r.refusal = "分散が 0 の群があり、等分散を仮定しない検定を計算できません。"
        return r

    if equal_var:
        r.omnibus_name = "一元配置 ANOVA（等分散を仮定）"
        r.F, r.df1, r.df2, r.p_omnibus, r.eta_squared = one_way_anova(samples)
        if goal == "vs_control":
            ci = groups.index(control)
            others = [g for g in groups if g != control]
            r.pairs = dunnett_pairs(control, samples[ci], others, [s for g, s in zip(groups, samples) if g != control])
            r.posthoc_name = "Dunnett（対照群との比較、多重性を調整）"
        else:
            r.pairs = tukey_pairs(groups, samples)
            r.posthoc_name = "Tukey（全群間の比較、Tukey–Kramer で多重性を調整）"
        r.notes.extend(ASSUMPTIONS_MULTI["anova"])
    else:
        r.omnibus_name = "Welch の一元配置 ANOVA（等分散を仮定しない）"
        r.F, r.df1, r.df2, r.p_omnibus = welch_anova(samples)
        _f, _d1, _d2, _p, r.eta_squared = one_way_anova(samples)
        if goal == "vs_control":
            r.pairs = games_howell_pairs(groups, samples, only_with=control)
            r.posthoc_name = "Games–Howell（対照群を含む対のみ表示。全対で多重性を調整するため保守的）"
        else:
            r.pairs = games_howell_pairs(groups, samples)
            r.posthoc_name = "Games–Howell（全群間の比較、多重性を調整）"
        r.notes.extend(ASSUMPTIONS_MULTI["welch"])
    r.ok = True
    r.title = f"{r.omnibus_name} ＋ {r.posthoc_name}"
    r.notes.append("η²（イータ二乗）は全体のばらつきのうち群の違いで説明される割合（参考値）。")
    r.notes.append("多重比較の p 値は調整済み（p_adj）です。全体検定（ANOVA）の結果に関わらず、事前に決めた比較の結果を報告してください。")
    if min(r.n.values()) < 3:
        r.notes.append("n < 3 の群があります。検定結果はきわめて不安定です。")
    if value_col in ("fold", "log2_fold"):
        r.notes.append("fold／log2_fold は全値を同じ定数で割った（引いた）値なので、比／log2_ratio で検定しても p 値は同じです。")
    r.notes.append("対照群の平均で割った値は、対照群の各値も同じ基準値を共有します。基準値の推定誤差は群間比較に含まれます。")
    return r
