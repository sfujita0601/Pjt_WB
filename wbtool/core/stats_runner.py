"""プロジェクトの統計設定から検定を選んで実行し、表・Figure 注記・説明文を作る（GUI 非依存）。

手法の案内（stat_goal × stat_design × stat_equal_var）
- 2群・独立           → Welch の t 検定
- 2群・対応あり       → 対応のある t 検定（subject_id で対応）
- 3群以上・対照群と比較 → Dunnett（等分散）／Welch ANOVA＋Games–Howell（不等分散、既定）
- 3群以上・全群間     → Tukey（等分散）／Welch ANOVA＋Games–Howell（不等分散、既定）
- 3群以上・対応あり   → 未実装のため検定しない
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..models.project import AnalysisSettings
from .figure import StatAnnotation
from .normalize import NormalizationResult
from .stats import TestResult, two_group_test
from .stats_multi import MultiResult, multi_group_test

GOALS = {
    "two": "2つの群を比べたい",
    "vs_control": "3群以上：それぞれの処理群を対照群と比べたい",
    "all_pairs": "3群以上：すべての群どうしを比べたい",
}
DESIGNS = {
    "independent": "独立（群ごとに別の個体・別の試料）",
    "paired": "対応あり（同じ個体・同じ試料を各条件で測った。subject_id が必要）",
}
VALUES = {
    "ratio": "正規化比（線形スケール）",
    "log2_ratio": "log2（正規化比）",
}


@dataclass
class StatsOutcome:
    ok: bool
    kind: str                                # "two" | "multi"
    method: str = ""
    refusal: str = ""
    two: TestResult | None = None
    multi: MultiResult | None = None
    table: pd.DataFrame = field(default_factory=pd.DataFrame)   # 比較ごとの結果（CSV 出力用）
    annotations: list[StatAnnotation] = field(default_factory=list)
    description: str = ""                    # 図の説明文に入れる統計の記述
    notes: list[str] = field(default_factory=list)


def value_column(settings: AnalysisSettings) -> str:
    v = settings.stat_value
    return {"fold": "ratio", "log2_fold": "log2_ratio"}.get(v, v if v in VALUES else "ratio")


def run_stats(settings: AnalysisSettings, norm: NormalizationResult) -> StatsOutcome:
    if norm.blocking:
        return StatsOutcome(False, "two", refusal=" ".join(norm.blocking))
    bio = norm.bio_table
    val = value_column(settings)
    scale = "log2（正規化比）" if val == "log2_ratio" else "正規化比"
    if settings.stat_goal == "two":
        test = "paired" if settings.stat_design == "paired" else "welch"
        r = two_group_test(bio, val, settings.stat_group_a, settings.stat_group_b, test)
        out = StatsOutcome(r.ok, "two", r.test_name, r.refusal, two=r, notes=list(r.notes))
        if r.ok:
            out.table = pd.DataFrame([{
                "group_a": r.group_a, "group_b": r.group_b, "value": val, "n_a": r.n_a, "n_b": r.n_b,
                "n_pairs": r.n_pairs, "diff": r.mean_diff, "ci_low": r.ci_low, "ci_high": r.ci_high,
                "statistic": r.statistic, "df": r.df, "p": r.p_value, "p_adj": r.p_value,
                "effect_name": r.effect_name, "effect_size": r.effect_size, "n_missing": r.n_missing, "method": r.test_name}])
            out.annotations = [StatAnnotation(r.group_a, r.group_b, r.p_value, r.test_name)]
            out.description = (f"統計：{scale} について {r.test_name}（両側、解析単位は生物学的反復、"
                               f"{r.group_a} n = {r.n_a}、{r.group_b} n = {r.n_b}）。")
        return out
    present = list(dict.fromkeys(bio["group"])) if len(bio) else []
    # 群を明示的に選んでいない場合は、画面の表示（全群にチェック）と同じく全群を使う
    groups = [g for g in settings.stat_groups if g in present] if settings.stat_groups else present
    m = multi_group_test(bio, val, groups, settings.stat_goal, settings.control_group,
                         settings.stat_equal_var, settings.stat_design)
    out = StatsOutcome(m.ok, "multi", m.title, m.refusal, multi=m, notes=list(m.notes))
    if m.ok:
        t = m.pairs.copy()
        t.insert(2, "value", val)
        t["omnibus"] = m.omnibus_name
        t["F"], t["df1"], t["df2"], t["p_omnibus"] = m.F, m.df1, m.df2, m.p_omnibus
        t["n_a"] = t["group_a"].map(m.n)
        t["n_b"] = t["group_b"].map(m.n)
        out.table = t
        out.annotations = [StatAnnotation(r.group_a, r.group_b, r.p_adj, r.method) for r in t.itertuples()]
        ns = "、".join(f"{g} n = {n}" for g, n in m.n.items())
        out.description = (f"統計：{scale} について {m.omnibus_name}、多重比較は {m.posthoc_name}。"
                           f"図中の p は調整済み p 値（解析単位は生物学的反復、{ns}）。")
    return out


def stats_table_csv(outcome: StatsOutcome, path) -> None:
    t = outcome.table.copy()
    t["notes"] = " / ".join(outcome.notes)
    t.to_csv(path, index=False, encoding="utf-8-sig")


__all__ = ["GOALS", "DESIGNS", "VALUES", "StatsOutcome", "run_stats", "stats_table_csv", "value_column"]
