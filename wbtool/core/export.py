"""CSV / JSON / ROI オーバーレイ画像の出力と、試料表 CSV の取込。GUI 非依存。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import __version__
from ..models.project import BG_LABELS, Project, Sample
from .imageio import dtype_max

SAMPLE_COLUMNS = ["sample_id", "group", "bio_rep", "tech_rep", "subject_id", "batch_id", "notes", "image_id", "lane_id"]


def samples_to_csv(project: Project, path: str | Path) -> None:
    rows = []
    for s in project.samples:
        lanes = [(im.image_id, l.lane_id) for im in project.images for l in im.lanes if l.sample_id == s.sample_id]
        if not lanes:
            lanes = [("", "")]
        for iid, lid in lanes:
            rows.append({"sample_id": s.sample_id, "group": s.group, "bio_rep": s.bio_rep, "tech_rep": s.tech_rep,
                         "subject_id": s.subject_id, "batch_id": s.batch_id, "notes": s.notes,
                         "image_id": iid, "lane_id": lid})
    pd.DataFrame(rows, columns=SAMPLE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")


def samples_from_csv(project: Project, path: str | Path) -> list[str]:
    """試料表を取り込む（既存 sample_id は上書き更新）。image_id と lane_id があればレーンに割り当てる。

    返り値：取り込み時のメッセージ。
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    if "sample_id" not in df.columns:
        raise ValueError("CSV に sample_id 列がありません。1行目を列名（sample_id, group, bio_rep, tech_rep, ...）にしてください。")
    msgs = []
    n_new = n_upd = n_lane = 0
    for _, r in df.iterrows():
        sid = r["sample_id"].strip()
        if not sid:
            continue
        s = project.sample(sid)
        if s is None:
            s = Sample(sid)
            project.samples.append(s)
            n_new += 1
        else:
            n_upd += 1
        for col in ("group", "bio_rep", "tech_rep", "subject_id", "batch_id", "notes"):
            if col in df.columns and r[col].strip() != "":
                setattr(s, col, r[col].strip())
        iid, lid = r.get("image_id", "").strip(), r.get("lane_id", "").strip()
        if iid and lid:
            im = project.image(iid)
            lane = im.lane(lid) if im else None
            if lane is None:
                msgs.append(f"{iid}/{lid} が見つからないため、{sid} のレーン割当を飛ばしました。")
            else:
                lane.sample_id = sid
                n_lane += 1
    msgs.insert(0, f"新規 {n_new} 件、更新 {n_upd} 件、レーン割当 {n_lane} 件を取り込みました。")
    return msgs


def roi_table(project: Project) -> pd.DataFrame:
    rows = []
    for im in project.images:
        for l in im.lanes:
            rows.append({"image_id": im.image_id, "kind": "lane", "id": l.lane_id, "lane_id": l.lane_id,
                         "sample_id": l.sample_id, "x": l.rect.x, "y": l.rect.y, "w": l.rect.w, "h": l.rect.h,
                         "protein": "", "bg_method": ""})
        for b in im.bands:
            rows.append({"image_id": im.image_id, "kind": "band", "id": b.band_id, "lane_id": b.lane_id,
                         "sample_id": (im.lane(b.lane_id).sample_id if im.lane(b.lane_id) else ""),
                         "x": b.rect.x, "y": b.rect.y, "w": b.rect.w, "h": b.rect.h,
                         "protein": im.band_protein(b), "bg_method": im.band_bg_method(b)})
        for i, r in enumerate(im.bg_rois, 1):
            rows.append({"image_id": im.image_id, "kind": "background", "id": f"BG{i}", "lane_id": "", "sample_id": "",
                         "x": r.x, "y": r.y, "w": r.w, "h": r.h, "protein": "", "bg_method": "manual_roi"})
    return pd.DataFrame(rows)


def settings_dict(project: Project) -> dict:
    return {
        "app": "wbtool",
        "app_version": __version__,
        "project": project.name,
        "formula": "S = Σ_i s·(p_i − b), s = −1（暗いバンド）/ +1（明るいバンド）。負の値は丸めない。",
        "background_methods": BG_LABELS,
        "images": [{
            "image_id": im.image_id, "path": im.path, "sha256": im.sha256, "bit_depth": im.bit_depth,
            "dtype": im.dtype, "width": im.width, "height": im.height, "file_format": im.file_format,
            "color_converted": im.color_converted, "protein_target": im.protein_target,
            "experiment_id": im.experiment_id, "blot_id": im.blot_id, "membrane_id": im.membrane_id,
            "exposure_id": im.exposure_id, "polarity": im.polarity, "include": im.include,
            "bg_method": im.bg_method, "bg_gap": im.bg_gap, "bg_height": im.bg_height,
        } for im in project.images],
        "analysis": {
            "numerator_protein": project.settings.numerator_protein,
            "denominator_protein": project.settings.denominator_protein,
            "control_group": project.settings.control_group,
            "stat_test": project.settings.stat_test,
            "stat_groups": [project.settings.stat_group_a, project.settings.stat_group_b],
            "figure": project.settings.figure,
        },
        "qc_decisions": {k: vars(v) for k, v in project.qc_decisions.items()},
    }


def write_settings_json(project: Project, path: str | Path) -> None:
    Path(path).write_text(json.dumps(settings_dict(project), ensure_ascii=False, indent=2), encoding="utf-8")


def display_copy(data: np.ndarray, low_pct: float = 0.5, high_pct: float = 99.5, invert: bool = False) -> np.ndarray:
    """表示用の 8-bit コピーを作る（原画像は変更しない）。"""
    d = data.astype(np.float64)
    lo, hi = np.percentile(d, [low_pct, high_pct])
    if hi <= lo:
        lo, hi = 0.0, float(dtype_max(data.dtype))
    out = np.clip((d - lo) / (hi - lo), 0, 1)
    if invert:
        out = 1 - out
    return (out * 255).astype(np.uint8)


def write_overlay_png(project: Project, image_id: str, data: np.ndarray, path: str | Path) -> None:
    """ROI を重ねた表示用画像を保存（自動コントラストは表示用コピーにのみ適用）。"""
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    from .figure import japanese_font_candidates
    from .quant import local_background_rects

    fonts = japanese_font_candidates()
    with matplotlib.rc_context({"font.family": fonts[0] if fonts else "DejaVu Sans"}):
        im = project.image(image_id)
        h, w = data.shape
        fig = Figure(figsize=(max(w / 150, 4), max(h / 150, 3)), dpi=150)
        ax = fig.add_subplot(111)
        ax.imshow(display_copy(data), cmap="gray", vmin=0, vmax=255, interpolation="nearest")
        for l in im.lanes:
            ax.add_patch(Rectangle((l.rect.x - .5, l.rect.y - .5), l.rect.w, l.rect.h, fill=False, ec="#00A0FF", lw=0.8, ls="--"))
            ax.text(l.rect.x, l.rect.y - 2, f"{l.lane_id}:{l.sample_id}", color="#00A0FF", fontsize=6, va="bottom")
        for b in im.bands:
            ax.add_patch(Rectangle((b.rect.x - .5, b.rect.y - .5), b.rect.w, b.rect.h, fill=False, ec="#FF3030", lw=1))
            ax.text(b.rect.x, b.rect.y2 + 1, b.band_id, color="#FF3030", fontsize=6, va="top")
            if im.band_bg_method(b) == "local":
                for r in local_background_rects(b.rect, im.bg_gap, im.bg_height, w, h):
                    ax.add_patch(Rectangle((r.x - .5, r.y - .5), r.w, r.h, fill=False, ec="#FFC000", lw=0.6))
        for r in im.bg_rois:
            ax.add_patch(Rectangle((r.x - .5, r.y - .5), r.w, r.h, fill=False, ec="#30D030", lw=1))
        ax.set_title(f"{image_id}  {im.protein_target}  (表示用コピー・自動コントラスト／測定は原画像値)", fontsize=7)
        ax.axis("off")
        fig.savefig(path, dpi=150, bbox_inches="tight")


def export_all(project: Project, measurements: pd.DataFrame, norm_sample: pd.DataFrame | None,
               norm_bio: pd.DataFrame | None, image_data: dict[str, np.ndarray], out_dir: str | Path) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    p = out / "measurements.csv"; measurements.to_csv(p, index=False, encoding="utf-8-sig"); written.append(p)
    p = out / "roi_list.csv"; roi_table(project).to_csv(p, index=False, encoding="utf-8-sig"); written.append(p)
    p = out / "settings.json"; write_settings_json(project, p); written.append(p)
    p = out / "samples.csv"; samples_to_csv(project, p); written.append(p)
    if norm_sample is not None and len(norm_sample):
        p = out / "normalized_by_sample.csv"; norm_sample.to_csv(p, index=False, encoding="utf-8-sig"); written.append(p)
    if norm_bio is not None and len(norm_bio):
        p = out / "normalized_by_biorep.csv"; norm_bio.to_csv(p, index=False, encoding="utf-8-sig"); written.append(p)
    hist = pd.DataFrame([vars(h) for h in project.history])
    p = out / "history.csv"; hist.to_csv(p, index=False, encoding="utf-8-sig"); written.append(p)
    for iid, data in image_data.items():
        im = project.image(iid)
        if im and (im.bands or im.lanes or im.bg_rois):
            p = out / f"overlay_{iid}.png"; write_overlay_png(project, iid, data, p); written.append(p)
    return [str(x) for x in written]
