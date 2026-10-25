"""Fiji/ImageJ との照合（GUI 非依存）。

1. imagej_macro()：本アプリと同じ ROI（バンド・近傍背景帯・手動背景 ROI）を Fiji で測るマクロを作る。
2. compare_with_fiji()：Fiji の Results CSV を読み、ROI ごとに面積・平均・RawIntDen を比較し、
   Fiji の値から同じ定義で背景値 b と S を再計算して本アプリの値と比較する。
   一致しない場合は原因の候補（ROI、極性、bit 深度、画素値の違い）を表示する。
3. compare_normalization()：Fiji から再計算した S で正規化比・fold を計算し、本アプリと比較する。

比較の前提：Fiji の RawIntDen は ROI 内の画素値の総和（空間較正に依存しない）＝本アプリの raw_sum。
Gel Analyzer のプロファイル面積は定義が異なるため、ここでは比較しない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..models.project import BG_LOCAL, BG_MANUAL, BG_NONE, POLARITY_DARK, Project
from .imageio import dtype_max
from .normalize import normalize
from .quant import local_background_rects

ID_COL = "wbtool_id"
REL_TOL = 1e-6      # Fiji の出力は小数 6 桁。整数の総和は本来完全一致する


def roi_specs(project: Project, image_id: str) -> list[dict]:
    """照合に使う ROI の一覧（id, kind, band_id, rect）。"""
    im = project.image(image_id)
    specs = []
    for b in im.bands:
        specs.append({"id": f"{b.band_id}:band", "kind": "band", "band_id": b.band_id, "rect": b.rect})
        if im.band_bg_method(b) == BG_LOCAL:
            for k, r in zip(("bg_above", "bg_below"), _local_with_side(b.rect, im)):
                if r is not None:
                    specs.append({"id": f"{b.band_id}:{k}", "kind": k, "band_id": b.band_id, "rect": r})
    for i, r in enumerate(im.bg_rois, 1):
        rc = r.clipped(im.width, im.height)
        if rc.area == 0:
            # 画像外の ROI は本アプリの計算でも除外される。ImageJ では幅 0 の選択が画像全体の測定になるため出力しない
            continue
        specs.append({"id": f"BG{i}:bg_manual", "kind": "bg_manual", "band_id": "", "rect": rc})
    return specs


def _local_with_side(band, im):
    rects = local_background_rects(band, im.bg_gap, im.bg_height, im.width, im.height)
    above = next((r for r in rects if r.y2 <= band.y), None)
    below = next((r for r in rects if r.y >= band.y2), None)
    return above, below


def imagej_macro(project: Project, image_id: str, results_path: str = "") -> str:
    im = project.image(image_id)
    out = results_path or str(Path(im.path).with_name(f"fiji_results_{image_id}.csv"))
    lines = [
        f"// wbtool {project.app_version} による Fiji/ImageJ 照合用マクロ",
        f"// 画像 {image_id}: {im.path}",
        f"// SHA-256: {im.sha256}",
        "// ROI 座標は原画像の画素（左上原点、整数）。Edit>Invert や 8-bit 変換などの画像処理をしないでください。",
        "// （表示の LUT・明るさ／コントラスト調整は画素値を変えないので問題ありません）",
        f'open("{_esc(im.path)}");',
        'run("Set Scale...", "distance=0 known=0 unit=pixel");   // 空間較正を外す（IntDen と RawIntDen を一致させる）',
        'run("Set Measurements...", "area mean min integrated redirect=None decimal=6");',
        'run("Clear Results");',
    ]
    for s in roi_specs(project, image_id):
        r = s["rect"]
        lines += [f"makeRectangle({r.x}, {r.y}, {r.w}, {r.h});", 'run("Measure");',
                  f'setResult("{ID_COL}", nResults-1, "{s["id"]}");']
    lines += ["updateResults();", f'saveAs("Results", "{_esc(out)}");',
              f'print("保存しました: {_esc(out)}");']
    return "\n".join(lines) + "\n"


def _esc(path: str) -> str:
    return path.replace("\\", "/").replace('"', '\\"')


def read_fiji_results(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    if ID_COL not in df.columns:
        raise ValueError(f"{ID_COL} 列がありません。本アプリが出力したマクロで測定した Results を保存してください。")
    for col in ("Area", "Mean", "RawIntDen"):
        if col not in df.columns:
            raise ValueError(f"{col} 列がありません。Set Measurements で Area・Mean・Integrated density を選んでください。")
    return df


@dataclass
class ValidationReport:
    image_id: str
    roi_table: pd.DataFrame          # ROI ごとの比較
    band_table: pd.DataFrame         # バンドごとの b・S の比較
    all_match: bool
    notes: list[str] = field(default_factory=list)


def _close(a, b, rel=REL_TOL) -> bool:
    if pd.isna(a) or pd.isna(b):
        return False
    return bool(np.isclose(a, b, rtol=rel, atol=1e-6))


def diagnose(ours: dict, fiji: pd.Series, vmax: int) -> str:
    """一致しない ROI の原因候補を返す。"""
    if not _close(ours["area"], fiji["Area"], 0):
        return ("ROI の位置・大きさが一致していません（面積が違う）。Fiji 側で ROI を動かした、"
                "またはマクロと異なる画像サイズの可能性があります。")
    if _close(vmax - ours["mean"], fiji["Mean"], 1e-6):
        return "画素極性が逆です。Fiji で Edit>Invert（画素値の反転）を実行した可能性があります。"
    if vmax == 65535 and ours["mean"] > 255 and fiji.get("Max", fiji["Mean"]) <= 255:
        # ImageJ の 16→8-bit 変換は表示範囲（min–max）で再スケールするため、単純な 1/257 にはならない
        return "Fiji 側で 8-bit に変換された可能性があります（Fiji の値が 0–255 の範囲に収まっています）。"
    if vmax == 255 and _close(ours["mean"] * 257.0, fiji["Mean"], 2e-3):
        return "Fiji 側で 16-bit に変換された可能性があります（値がおよそ 257 倍）。"
    return ("画素値が一致しません。別の画像ファイル、Fiji 側での画像処理（背景除去・平滑化）、"
            "カラー画像のグレースケール変換の重みの違いなどを確認してください。")


def compare_with_fiji(project: Project, image_id: str, data: np.ndarray, fiji: pd.DataFrame,
                      measurements: pd.DataFrame) -> ValidationReport:
    im = project.image(image_id)
    vmax = dtype_max(data.dtype)
    fiji = fiji.set_index(ID_COL)
    rows = []
    vals: dict[str, dict] = {}
    for s in roi_specs(project, image_id):
        r = s["rect"].clipped(im.width, im.height)
        px = data[r.y:r.y2, r.x:r.x2].astype(np.float64)
        ours = {"area": px.size, "mean": px.mean() if px.size else np.nan, "raw_sum": px.sum()}
        row = {"wbtool_id": s["id"], "kind": s["kind"], "x": r.x, "y": r.y, "w": r.w, "h": r.h,
               "area_app": ours["area"], "area_fiji": np.nan, "mean_app": ours["mean"], "mean_fiji": np.nan,
               "rawintden_app": ours["raw_sum"], "rawintden_fiji": np.nan, "match": False, "diagnosis": ""}
        if s["id"] not in fiji.index:
            row["diagnosis"] = "Fiji の結果にこの ROI がありません（マクロを最後まで実行したか確認してください）。"
        else:
            f = fiji.loc[s["id"]]
            if isinstance(f, pd.DataFrame):
                f = f.iloc[-1]
            row.update(area_fiji=float(f["Area"]), mean_fiji=float(f["Mean"]), rawintden_fiji=float(f["RawIntDen"]))
            row["match"] = (_close(ours["area"], f["Area"], 0) and _close(ours["raw_sum"], f["RawIntDen"])
                            and _close(ours["mean"], f["Mean"]))
            row["diagnosis"] = "一致" if row["match"] else diagnose(ours, f, vmax)
            vals[s["id"]] = {"area": float(f["Area"]), "raw": float(f["RawIntDen"])}
        rows.append(row)
    roi_table = pd.DataFrame(rows)

    # バンドごとに、Fiji の値から同じ定義で b と S を再計算
    meas = measurements[measurements["image_id"] == image_id].set_index("band_id") if len(measurements) else None
    sign = -1 if im.polarity == POLARITY_DARK else 1
    brow = []
    manual_ids = [k for k in vals if k.endswith(":bg_manual")]
    for b in im.bands:
        method = im.band_bg_method(b)
        band = vals.get(f"{b.band_id}:band")
        if method == BG_LOCAL:
            parts = [vals[k] for k in (f"{b.band_id}:bg_above", f"{b.band_id}:bg_below") if k in vals]
        elif method == BG_MANUAL:
            parts = [vals[k] for k in manual_ids]
        else:
            parts = []
        if method == BG_NONE:
            b_fiji = float(vmax) if im.polarity == POLARITY_DARK else 0.0
        elif parts:
            # 背景値は画素数で重み付けした平均 = Σ RawIntDen / Σ Area
            b_fiji = sum(p["raw"] for p in parts) / sum(p["area"] for p in parts)
        else:
            b_fiji = np.nan
        s_fiji = sign * (band["raw"] - band["area"] * b_fiji) if band else np.nan
        m = meas.loc[b.band_id] if meas is not None and b.band_id in meas.index else None
        b_app = float(m["bg_value"]) if m is not None else np.nan
        s_app = float(m["corrected"]) if m is not None else np.nan
        both_nan = pd.isna(b_app) and pd.isna(b_fiji)
        ok = _close(b_app, b_fiji) and _close(s_app, s_fiji, 1e-6)
        if both_nan:
            diag = "本アプリ・Fiji とも背景値を計算できません（手動背景 ROI が無い等）。照合の対象外です。"
        else:
            diag = "一致" if ok else _band_diag(roi_table, b.band_id)
        brow.append({"band_id": b.band_id, "bg_method": method, "bg_app": b_app, "bg_fiji": b_fiji,
                     "S_app": s_app, "S_fiji": s_fiji, "S_diff": s_app - s_fiji, "match": ok or both_nan,
                     "diagnosis": diag})
    band_table = pd.DataFrame(brow)
    notes = ["比較している量：Area（画素数）、Mean（平均画素値）、RawIntDen（画素値の総和）。",
             "S は Fiji の測定値から同じ式 S = s·(RawIntDen_band − Area_band × b) で再計算した値と比較しています。",
             "Gel Analyzer のピーク面積は測定の定義が異なるため、一致しなくても誤りではありません（比較対象外）。"]
    all_match = bool(len(roi_table)) and bool(roi_table["match"].all()) and bool(band_table["match"].all() if len(band_table) else True)
    return ValidationReport(image_id, roi_table, band_table, all_match, notes)


def _band_diag(roi_table: pd.DataFrame, band_id: str) -> str:
    bad = roi_table[(roi_table["wbtool_id"].str.startswith(f"{band_id}:")) & (~roi_table["match"])]
    if len(bad):
        return "この ROI に関わる測定が一致していません：" + " / ".join(f"{r.wbtool_id}：{r.diagnosis}" for r in bad.itertuples())
    return "ROI ごとの値は一致しているが b または S が一致しません。背景法（手動背景 ROI の数など）の設定を確認してください。"


def fiji_measurement_table(measurements: pd.DataFrame, reports: list[ValidationReport]) -> pd.DataFrame:
    """測定表の S と b を Fiji 由来の値に置き換えたもの（正規化の比較用）。"""
    df = measurements.copy()
    for rep in reports:
        for r in rep.band_table.itertuples():
            mask = (df["image_id"] == rep.image_id) & (df["band_id"] == r.band_id)
            df.loc[mask, "corrected"] = r.S_fiji
            df.loc[mask, "bg_value"] = r.bg_fiji
    return df


def compare_normalization(project: Project, measurements: pd.DataFrame, reports: list[ValidationReport]) -> pd.DataFrame:
    s = project.settings
    ours = normalize(measurements, s.numerator_protein, s.denominator_protein, s.control_group, project.samples)
    fij = normalize(fiji_measurement_table(measurements, reports), s.numerator_protein, s.denominator_protein,
                    s.control_group, project.samples)
    a = ours.bio_table.set_index(["group", "bio_rep"])[["ratio", "fold"]]
    b = fij.bio_table.set_index(["group", "bio_rep"])[["ratio", "fold"]]
    out = a.add_suffix("_app").join(b.add_suffix("_fiji"), how="outer").reset_index()
    out["match"] = [(_close(r.ratio_app, r.ratio_fiji) and (_close(r.fold_app, r.fold_fiji) or (pd.isna(r.fold_app) and pd.isna(r.fold_fiji))))
                    for r in out.itertuples()]
    return out


def report_markdown(project: Project, reports: list[ValidationReport], norm: pd.DataFrame | None) -> str:
    """照合結果を記録用の Markdown にする（表は CSV 形式のコードブロック）。"""
    lines = [f"# Fiji/ImageJ 照合結果（{project.name}）", ""]
    for rep in reports:
        im = project.image(rep.image_id)
        lines += [f"## {rep.image_id}（{im.protein_target}） — {'すべて一致' if rep.all_match else '不一致あり'}",
                  f"- 画像：{im.path}", f"- SHA-256：{im.sha256}", "", "### ROI ごと", "```csv",
                  rep.roi_table.to_csv(index=False).strip(), "```", "", "### バンドごとの背景値 b と S", "```csv",
                  rep.band_table.to_csv(index=False).strip(), "```", ""]
        lines += [f"- {n}" for n in rep.notes] + [""]
    if norm is not None:
        lines += ["## 正規化比・fold の比較", "```csv", norm.to_csv(index=False).strip(), "```"]
    return "\n".join(lines) + "\n"
