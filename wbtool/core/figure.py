"""定量グラフの作成（matplotlib）。GUI 非依存。

点は常に生物学的反復（normalize.bio_table の1行）。エラーバーは SD（既定）／SEM／95%CI を選べ、
図の説明文（エラーバーの種類・n・検定法）を自動生成する。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import matplotlib
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
from scipy import stats as sps

PLOT_KINDS = {
    "dots_mean_sd": "個々の点＋平均±エラーバー（推奨）",
    "bar_dots": "棒（平均）＋エラーバー＋個々の点",
    "box_dots": "箱ひげ図＋個々の点",
}
ERROR_BARS = {
    "sd": "SD（標準偏差：データのばらつき）",
    "sem": "SEM（標準誤差：平均の推定精度）",
    "ci95": "95% 信頼区間（平均の区間推定）",
}
VALUE_LABELS = {
    "fold": "対照群を 1 とした相対値（fold change）",
    "ratio": "正規化比（Target ÷ ローディングコントロール）",
    "log2_ratio": "log2（正規化比）",
    "log2_fold": "log2（fold change、対照群の幾何平均 = 0）",
    "fold_geo": "対照群の幾何平均を 1 とした相対値",
}
DEFAULT_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860"]


@dataclass
class FigureStyle:
    kind: str = "dots_mean_sd"
    value_col: str = "fold"
    error_bar: str = "sd"
    title: str = ""
    xlabel: str = ""
    ylabel: str = "対照群を 1 とした相対値（fold change）"
    group_order: list[str] = field(default_factory=list)
    group_labels: dict[str, str] = field(default_factory=dict)
    colors: list[str] = field(default_factory=lambda: list(DEFAULT_COLORS))
    font_family: str = ""
    font_size: float = 10.0
    ymin: float | None = None
    ymax: float | None = None
    width_in: float = 3.0
    height_in: float = 3.0
    dpi: int = 300
    point_size: float = 30.0
    show_n: bool = True
    show_stats: bool = False        # 調整済み p 値を図に表示する

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FigureStyle":
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in names})


@dataclass
class StatAnnotation:
    group_a: str
    group_b: str
    p_adj: float
    method: str = ""


def japanese_font_candidates() -> list[str]:
    from matplotlib import font_manager
    wanted = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic", "Noto Sans CJK JP", "Noto Sans JP",
              "IPAexGothic", "MS Gothic", "Meiryo"]
    have = {f.name for f in font_manager.fontManager.ttflist}
    return [w for w in wanted if w in have]


def error_half_width(vals: np.ndarray, kind: str) -> float:
    n = vals.size
    if n < 2:
        return float("nan")
    sd = vals.std(ddof=1)
    if kind == "sd":
        return float(sd)
    if kind == "sem":
        return float(sd / np.sqrt(n))
    if kind == "ci95":
        return float(sps.t.ppf(0.975, n - 1) * sd / np.sqrt(n))
    raise ValueError(f"不明なエラーバー: {kind}")


def format_p(p: float) -> str:
    if not np.isfinite(p):
        return "p = —"
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}"


def ordered_groups(bio: pd.DataFrame, style: FigureStyle) -> list[str]:
    if not len(bio):
        return []
    present = list(dict.fromkeys(bio["group"]))
    groups = [g for g in style.group_order if g in present]
    return groups + [g for g in present if g not in groups]


def plot_groups(bio: pd.DataFrame, style: FigureStyle, fig: Figure | None = None,
                annotations: list[StatAnnotation] | None = None, ax=None) -> Figure:
    """生物学的反復の値（bio_table）を群ごとに描く。ax を渡すとその軸に描く（複合 Figure 用）。"""
    if ax is None:
        if fig is None:
            fig = Figure(figsize=(style.width_in, style.height_in), dpi=100)
        fig.clear()
        fig.set_size_inches(style.width_in, style.height_in)
    family = style.font_family or (japanese_font_candidates() or ["DejaVu Sans"])[0]
    with matplotlib.rc_context({"font.family": family, "font.size": style.font_size, "svg.fonttype": "none",
                                "pdf.fonttype": 42, "axes.unicode_minus": False}):
        own_ax = ax is None
        if own_ax:
            ax = fig.add_subplot(111)
        groups = ordered_groups(bio, style)
        rng = np.random.default_rng(0)   # ジッターを再現可能にする
        tops = {}
        for i, g in enumerate(groups):
            vals = bio.loc[bio["group"] == g, style.value_col].astype(float).dropna().values
            color = style.colors[i % len(style.colors)] if style.colors else DEFAULT_COLORS[i % 6]
            if vals.size == 0:
                continue
            jitter = rng.uniform(-0.12, 0.12, vals.size) if vals.size > 1 else np.zeros(vals.size)
            mean = vals.mean()
            err = error_half_width(vals, style.error_bar)
            if style.kind == "bar_dots":
                ax.bar(i, mean, width=0.6, color=color, alpha=0.35, edgecolor=color, linewidth=1)
                if np.isfinite(err):
                    ax.errorbar(i, mean, yerr=err, color="black", capsize=5, linewidth=1)
            elif style.kind == "box_dots":
                ax.boxplot([vals], positions=[i], widths=0.5, showfliers=False,
                           medianprops={"color": "black"}, boxprops={"color": color}, whiskerprops={"color": color},
                           capprops={"color": color})
            else:
                ax.hlines(mean, i - 0.25, i + 0.25, color="black", linewidth=1.5)
                if np.isfinite(err):
                    ax.errorbar(i, mean, yerr=err, color="black", capsize=5, linewidth=1)
            ax.scatter(i + jitter, vals, s=style.point_size, color=color, edgecolor="black", linewidth=0.5, zorder=3)
            tops[g] = max(vals.max(), mean + (err if np.isfinite(err) else 0))
        labels = []
        for g in groups:
            lab = style.group_labels.get(g, g)
            if style.show_n:
                lab = f"{lab}\n(n={int(bio.loc[bio['group'] == g, style.value_col].notna().sum())})"
            labels.append(lab)
        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels(labels)
        ax.set_xlim(-0.6, max(len(groups) - 0.4, 0.6))
        ax.set_ylabel(style.ylabel)
        if style.xlabel:
            ax.set_xlabel(style.xlabel)
        if style.title:
            ax.set_title(style.title)
        lo, hi = ax.get_ylim()
        if style.value_col.startswith("log2"):
            ax.axhline(0, color="#999", linewidth=0.8, linestyle="--", zorder=0)
            base_lo = lo
        else:
            base_lo = min(lo, 0)
        if style.show_stats and annotations:
            hi = _draw_annotations(ax, groups, annotations, tops, lo, hi, style.font_size)
        ax.set_ylim(style.ymin if style.ymin is not None else base_lo, style.ymax if style.ymax is not None else hi)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if own_ax:
            fig.tight_layout()
    return fig if fig is not None else ax.figure


def _draw_annotations(ax, groups, annotations, tops, lo, hi, font_size) -> float:
    span = hi - lo if hi > lo else 1.0
    step = span * 0.11
    level = max(tops.values()) + step * 0.4 if tops else hi
    idx = {g: i for i, g in enumerate(groups)}
    for a in sorted(annotations, key=lambda a: abs(idx.get(a.group_b, 0) - idx.get(a.group_a, 0))):
        if a.group_a not in idx or a.group_b not in idx:
            continue
        x1, x2 = sorted((idx[a.group_a], idx[a.group_b]))
        ax.plot([x1, x1, x2, x2], [level, level + step * 0.25, level + step * 0.25, level], color="black", linewidth=0.8)
        ax.text((x1 + x2) / 2, level + step * 0.3, format_p(a.p_adj), ha="center", va="bottom", fontsize=font_size * 0.8)
        level += step
    return max(hi, level + step * 0.2)


def figure_caption(bio: pd.DataFrame, style: FigureStyle, stats_desc: str = "", normalization_desc: str = "") -> str:
    """図の説明文（凡例・Figure legend の下書き）を作る。"""
    groups = ordered_groups(bio, style)
    ns = ", ".join(f"{style.group_labels.get(g, g)} n = {int(bio.loc[bio['group'] == g, style.value_col].notna().sum())}"
                   for g in groups)
    center = "中央値と四分位範囲（箱）" if style.kind == "box_dots" else f"平均 ± {style.error_bar.upper() if style.error_bar != 'ci95' else '95% 信頼区間'}"
    parts = [
        f"縦軸：{VALUE_LABELS.get(style.value_col, style.value_col)}。",
        f"点は個々の生物学的反復（biological replicate）、{center}を示す（{ns}）。",
        "技術的反復（technical replicate）は生物学的反復ごとに平均した。",
    ]
    if normalization_desc:
        parts.append(normalization_desc)
    if stats_desc:
        parts.append(stats_desc)
    parts.append("バンド強度は較正していない相対的な画像強度（背景補正後の積分値）である。")
    return "".join(parts)


def save_figure(fig: Figure, path: str, dpi: int = 300) -> None:
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
