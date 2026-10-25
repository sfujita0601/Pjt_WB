"""Figure 用のバンド画像パネル（GUI 非依存）。

画像の誠実な取り扱いのための仕様
- 元画像の**連続した矩形領域**だけを切り出す。レーンの並べ替え・継ぎ合わせ（splicing）はしない。
- 画像ごとに別の段にし、画像 ID・タンパク質・膜・露光を各段に表示する（別画像・別膜の境界を隠さない）。
- 表示は線形の階調変換（下限・上限の2値）のみ。ガンマ補正・部分的な明るさ調整はしない。
- 切り出し座標・表示レベル・元画像の SHA-256 を記録し、切り出した画素は**元の値のまま** TIFF で保存できる。
- 原画像ファイルは変更しない。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ..models.project import Project, Rect
from .imageio import dtype_max


@dataclass
class PanelRow:
    image_id: str
    protein: str
    membrane_id: str
    exposure_id: str
    sha256: str
    crop: Rect                       # 原画像座標
    levels: tuple[float, float]      # 表示の下限・上限（原画像の画素値）
    level_mode: str                  # "crop_minmax" | "full_range"
    lanes: list[tuple[float, str]] = field(default_factory=list)   # (切り出し内の x 中心, ラベル)
    bands: list[Rect] = field(default_factory=list)                # 切り出し内座標の ROI
    pixels: np.ndarray | None = None  # 切り出した原画素（元の dtype のまま）

    def record(self) -> dict:
        d = asdict(self)
        d.pop("pixels")
        d["crop"] = self.crop.as_tuple()
        d["bands"] = [b.as_tuple() for b in self.bands]
        d["display_transform"] = "linear（下限→黒、上限→白、ガンマ補正なし）"
        return d


def crop_rect_for_image(project: Project, image_id: str, margin: int = 10) -> Rect | None:
    """その画像のレーン全体（左端〜右端）とバンドの縦範囲を含む、連続した矩形。"""
    im = project.image(image_id)
    if im is None or not im.bands:
        return None
    lanes = im.lanes or []
    xs = [l.rect.x for l in lanes] + [b.rect.x for b in im.bands]
    x2s = [l.rect.x2 for l in lanes] + [b.rect.x2 for b in im.bands]
    y1 = min(b.rect.y for b in im.bands) - margin
    y2 = max(b.rect.y2 for b in im.bands) + margin
    return Rect(min(xs), y1, max(x2s) - min(xs), y2 - y1).clipped(im.width, im.height)


def build_panel(project: Project, image_data: dict[str, np.ndarray], image_ids: list[str], margin: int = 10,
                level_mode: str = "crop_minmax", label: str = "sample_id") -> list[PanelRow]:
    rows = []
    for iid in image_ids:
        im = project.image(iid)
        data = image_data.get(iid)
        crop = crop_rect_for_image(project, iid, margin)
        if im is None or data is None or crop is None or crop.area == 0:
            continue
        px = np.array(data[crop.y:crop.y2, crop.x:crop.x2])     # コピー（原画像配列は変更しない）
        if level_mode == "full_range":
            levels = (0.0, float(dtype_max(data.dtype)))
        else:
            levels = (float(px.min()), float(px.max()))
            if levels[1] <= levels[0]:
                levels = (0.0, float(dtype_max(data.dtype)))
        lanes = []
        for l in sorted(im.lanes, key=lambda l: l.rect.x):
            s = project.sample(l.sample_id)
            text = l.sample_id if label == "sample_id" else (s.group if s and label == "group" else l.lane_id)
            lanes.append((l.rect.x + l.rect.w / 2 - crop.x, text or l.lane_id))
        bands = [Rect(b.rect.x - crop.x, b.rect.y - crop.y, b.rect.w, b.rect.h) for b in im.bands]
        rows.append(PanelRow(iid, im.protein_target, im.membrane_id, im.exposure_id, im.sha256, crop, levels,
                             level_mode, lanes, bands, px))
    return rows


def display_array(row: PanelRow) -> np.ndarray:
    """表示用の 0–1 配列（線形変換のみ）。"""
    lo, hi = row.levels
    return np.clip((row.pixels.astype(np.float64) - lo) / (hi - lo), 0, 1)


def draw_panel(fig, rows: list[PanelRow], top: float = 0.98, bottom: float = 0.02, show_rois: bool = False,
               font_size: float = 8.0, left: float = 0.30, right: float = 0.97) -> None:
    """fig の [bottom, top] の高さ範囲に、各画像を1段ずつ描く。"""
    if not rows:
        return
    from matplotlib.patches import Rectangle
    n = len(rows)
    h_each = (top - bottom) / n
    for i, row in enumerate(rows):
        y0 = top - (i + 1) * h_each
        ax = fig.add_axes([left, y0 + h_each * 0.12, right - left, h_each * 0.7])
        ax.imshow(display_array(row), cmap="gray", vmin=0, vmax=1, interpolation="nearest", aspect="auto")
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_linewidth(0.8)
        ax.text(-0.02, 0.5, f"{row.protein}\n{row.image_id}\n膜 {row.membrane_id or '—'}・露光 {row.exposure_id or '—'}",
                transform=ax.transAxes, ha="right", va="center", fontsize=font_size * 0.75)
        for x, text in row.lanes:   # 各段の上にレーンのラベル（段ごとにレーン位置が違ってもよい）
            ax.text(x, -0.6, text, ha="center", va="bottom", fontsize=font_size * 0.75, transform=ax.transData)
        if show_rois:
            for b in row.bands:
                ax.add_patch(Rectangle((b.x - 0.5, b.y - 0.5), b.w, b.h, fill=False, ec="#FF3030", lw=0.6))

PANEL_NOTE = "バンド画像：各段は別々の画像から切り出した連続領域（レーンの並べ替え・継ぎ合わせなし、線形表示）"


def composite_figure(project: Project, image_data: dict[str, np.ndarray], image_ids: list[str], bio, style,
                     annotations=None, margin: int = 10, level_mode: str = "crop_minmax", show_rois: bool = False,
                     fig=None, resize: bool = True):
    """バンド画像パネル（上）と定量グラフ（下）を1枚にまとめる。返り値：(Figure, PanelRow のリスト)。

    fig を渡すとそこに描く。画面に埋め込んだ Figure では resize=False にする（キャンバスの大きさを変えない）。
    """
    import matplotlib
    from matplotlib.figure import Figure
    from .figure import japanese_font_candidates, plot_groups

    rows = build_panel(project, image_data, image_ids, margin, level_mode)
    family = style.font_family or (japanese_font_candidates() or ["DejaVu Sans"])[0]
    panel_h = 0.75 * len(rows)
    size = (max(style.width_in, 4.5), style.height_in + panel_h + 0.4)
    if fig is None:
        fig = Figure(figsize=size)
    else:
        fig.clear()
        if resize:
            fig.set_size_inches(*size)
    with matplotlib.rc_context({"font.family": family, "font.size": style.font_size, "svg.fonttype": "none",
                                "pdf.fonttype": 42, "axes.unicode_minus": False}):
        total = style.height_in + panel_h + 0.4
        split = style.height_in / total
        draw_panel(fig, rows, top=0.98, bottom=split + 0.02, show_rois=show_rois, font_size=style.font_size)
        ax = fig.add_axes([0.30, 0.12 * split + 0.06, 0.66, split * 0.78])
        plot_groups(bio, style, annotations=annotations, ax=ax)
        if rows:
            fig.text(0.02, 0.005, PANEL_NOTE, fontsize=style.font_size * 0.6, va="bottom", color="#444")
    return fig, rows
