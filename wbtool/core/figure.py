"""定量グラフの作成（matplotlib）。GUI 非依存。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import matplotlib
from matplotlib.figure import Figure
import numpy as np
import pandas as pd

PLOT_KINDS = {
    "dots_mean_sd": "個々の点＋平均±SD",
    "bar_dots": "棒（平均）＋SD＋個々の点",
    "box_dots": "箱ひげ図＋個々の点",
}
DEFAULT_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860"]


@dataclass
class FigureStyle:
    kind: str = "dots_mean_sd"
    value_col: str = "fold"
    title: str = ""
    xlabel: str = ""
    ylabel: str = "相対量（fold to control）"
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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FigureStyle":
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in names})


def japanese_font_candidates() -> list[str]:
    from matplotlib import font_manager
    wanted = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic", "Noto Sans CJK JP", "Noto Sans JP",
              "IPAexGothic", "MS Gothic", "Meiryo"]
    have = {f.name for f in font_manager.fontManager.ttflist}
    return [w for w in wanted if w in have]


def plot_groups(bio: pd.DataFrame, style: FigureStyle, fig: Figure | None = None) -> Figure:
    """biological replicate 単位の値（bio_table）を群ごとに描く。"""
    if fig is None:
        fig = Figure(figsize=(style.width_in, style.height_in), dpi=100)
    fig.clear()
    fig.set_size_inches(style.width_in, style.height_in)
    family = style.font_family or (japanese_font_candidates() or ["DejaVu Sans"])[0]
    with matplotlib.rc_context({"font.family": family, "font.size": style.font_size, "svg.fonttype": "none",
                                "pdf.fonttype": 42, "axes.unicode_minus": False}):
        ax = fig.add_subplot(111)
        groups = [g for g in (style.group_order or []) if g in set(bio["group"])] if len(bio) else []
        if len(bio):
            groups += [g for g in dict.fromkeys(bio["group"]) if g not in groups]
        rng = np.random.default_rng(0)   # ジッターを再現可能にする
        for i, g in enumerate(groups):
            vals = bio.loc[bio["group"] == g, style.value_col].astype(float).dropna().values
            color = style.colors[i % len(style.colors)] if style.colors else DEFAULT_COLORS[i % 6]
            jitter = rng.uniform(-0.12, 0.12, vals.size) if vals.size > 1 else np.zeros(vals.size)
            if vals.size == 0:
                continue
            mean = vals.mean()
            sd = vals.std(ddof=1) if vals.size > 1 else np.nan
            if style.kind == "bar_dots":
                ax.bar(i, mean, width=0.6, color=color, alpha=0.35, edgecolor=color, linewidth=1)
                if np.isfinite(sd):
                    ax.errorbar(i, mean, yerr=sd, color="black", capsize=5, linewidth=1)
            elif style.kind == "box_dots":
                ax.boxplot([vals], positions=[i], widths=0.5, showfliers=False,
                           medianprops={"color": "black"}, boxprops={"color": color}, whiskerprops={"color": color},
                           capprops={"color": color})
            else:
                ax.hlines(mean, i - 0.25, i + 0.25, color="black", linewidth=1.5)
                if np.isfinite(sd):
                    ax.errorbar(i, mean, yerr=sd, color="black", capsize=5, linewidth=1)
            ax.scatter(i + jitter, vals, s=style.point_size, color=color, edgecolor="black", linewidth=0.5, zorder=3)
        labels = []
        for g in groups:
            lab = style.group_labels.get(g, g)
            if style.show_n:
                n = int(bio.loc[bio["group"] == g, style.value_col].notna().sum())
                lab = f"{lab}\n(n={n})"
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
        ax.set_ylim(style.ymin if style.ymin is not None else min(lo, 0), style.ymax if style.ymax is not None else hi)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()
    return fig


def save_figure(fig: Figure, path: str, dpi: int = 300) -> None:
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
