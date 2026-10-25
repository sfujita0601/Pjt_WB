"""品質管理（QC）チェック。GUI 非依存。

ここで出すのはすべて「候補・疑い」であり確定診断ではない。画素値の分布だけでは、
信号とタンパク質量の比例関係（線形範囲）は証明できない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from ..models.project import POLARITY_DARK, Project, Rect
from .imageio import dtype_max
from .quant import local_background_rects

LEVEL_ERROR = "エラー"
LEVEL_WARN = "警告"
LEVEL_NOTE = "注意"

EXTREME_FRACTION = 0.001   # 画像全体で端値画素が 0.1% を超えたら警告


@dataclass
class QCIssue:
    key: str
    level: str
    category: str
    message: str
    how_to_fix: str
    image_id: str = ""
    band_ids: list[str] = field(default_factory=list)


STUDENT_CHECKLIST = [
    "フィルムの露光時間を変えた複数枚のうち、目的バンドが飽和していない露光を選んだか（画像から飽和は「疑い」しか判定できない）。",
    "スキャナーの自動補正（自動レベル・コントラスト・シャープネス）を切ったか。透過モード・グレースケール・16-bit で取り込んだか。",
    "Target と Loading control の両方が線形範囲にあることを、希釈系列等で確認したか（未確認なら結果の解釈に注記する）。",
    "smear、ghost band、気泡、膜のしわ・汚れが ROI や背景 ROI に入っていないか目視で確認したか。",
    "同じ膜の別露光を独立な反復として数えていないか。",
    "各 sample_id に正しい群・biological replicate が割り当てられているか。",
    "除外したバンドについて、理由を記録したか（結果を見てから都合よく除外しない）。",
]


def check_image_extremes(data: np.ndarray, polarity: str) -> tuple[float, float]:
    vmax = dtype_max(data.dtype)
    n = data.size
    return float((data <= 0).sum() / n), float((data >= vmax).sum() / n)


def run_qc(project: Project, measurements: pd.DataFrame, image_data: dict[str, np.ndarray],
           load_errors: dict[str, str] | None = None,
           extremes_cache: dict[str, tuple[float, float]] | None = None) -> list[QCIssue]:
    """extremes_cache：画像ごとの端値画素の割合を保持する辞書（キーは画像パス＋ハッシュ）。

    画像ファイルは不変なので、同じ画像について毎回全画素を数え直さないために使う。
    """
    issues: list[QCIssue] = []
    add = issues.append
    for iid, err in (load_errors or {}).items():
        add(QCIssue(f"load:{iid}", LEVEL_ERROR, "画像ファイル", f"{iid} を読み込めません。{err}",
                    "［画像・試料情報］で再リンクしてください。", iid))

    sample_ids = {s.sample_id for s in project.samples}
    for s in project.samples:
        if not s.group:
            add(QCIssue(f"nogroup:{s.sample_id}", LEVEL_WARN, "試料情報", f"sample_id「{s.sample_id}」の群が未入力です。",
                        "［画像・試料情報］の試料表で group を入力してください。"))
        if not s.bio_rep:
            add(QCIssue(f"nobio:{s.sample_id}", LEVEL_WARN, "試料情報",
                        f"sample_id「{s.sample_id}」の biological replicate ID が未入力です。統計の独立単位が決まりません。",
                        "試料表で bio_rep を入力してください（同じ個体・同じ培養なら同じ ID）。"))

    for im in project.images:
        data = image_data.get(im.image_id)
        unused = "" if im.include else "（この画像は「定量に使う」がオフのため、正規化・統計には入りません）"
        if im.file_format.upper() in ("JPEG", "JPG"):
            add(QCIssue(f"jpeg:{im.image_id}", LEVEL_WARN, "画像形式",
                        f"{im.image_id} は JPEG（不可逆圧縮）です。", "可能なら TIFF/PNG で取り込み直してください。", im.image_id))
        if im.color_converted:
            add(QCIssue(f"color:{im.image_id}", LEVEL_NOTE, "画像形式",
                        f"{im.image_id} はカラー画像をグレースケール変換して測定しています。",
                        "スキャナーをグレースケールモードにして取り込むことを推奨します。", im.image_id))
        if data is not None:
            key = f"{im.path}|{im.sha256}"
            if extremes_cache is not None and key in extremes_cache:
                low, high = extremes_cache[key]
            else:
                low, high = check_image_extremes(data, im.polarity)
                if extremes_cache is not None:
                    extremes_cache[key] = (low, high)
            vmax = dtype_max(data.dtype)
            if low > EXTREME_FRACTION:
                add(QCIssue(f"low:{im.image_id}", LEVEL_WARN, "飽和・黒つぶれ候補",
                            f"{im.image_id}：画素値 0 の画素が {low:.2%} あります（黒つぶれの疑い）。{unused}",
                            "暗いバンドが飽和している可能性があります。短い露光のフィルムを使うか、スキャン設定を見直してください。",
                            im.image_id))
            if high > EXTREME_FRACTION:
                add(QCIssue(f"high:{im.image_id}", LEVEL_WARN, "飽和・白飛び候補",
                            f"{im.image_id}：画素値 {vmax} の画素が {high:.2%} あります（白飛びの疑い）。{unused}",
                            "背景が白飛びしていると背景推定が過小評価されます。スキャナーの自動補正を切って取り込み直してください。",
                            im.image_id))
        if not im.lanes and not im.bands:
            continue
        lane_ids = {l.lane_id for l in im.lanes}
        seen_samples: dict[str, list[str]] = {}
        for l in im.lanes:
            if not l.sample_id:
                add(QCIssue(f"nosample:{im.image_id}:{l.lane_id}", LEVEL_WARN, "対応付け",
                            f"{im.image_id} のレーン {l.lane_id} に sample_id がありません。",
                            "［レーン・バンドROI］のレーン表で sample_id を選んでください。", im.image_id))
            else:
                seen_samples.setdefault(l.sample_id, []).append(l.lane_id)
                if l.sample_id not in sample_ids:
                    add(QCIssue(f"unknownsample:{im.image_id}:{l.lane_id}", LEVEL_WARN, "対応付け",
                                f"{im.image_id} のレーン {l.lane_id} の sample_id「{l.sample_id}」が試料表にありません。",
                                "試料表に追加するか、sample_id の綴りを確認してください。", im.image_id))
            if not l.rect.inside(im.width, im.height):
                add(QCIssue(f"laneout:{im.image_id}:{l.lane_id}", LEVEL_NOTE, "ROI",
                            f"レーン {l.lane_id} が画像外にはみ出しています。", "レーンを画像内に移動してください。", im.image_id))
        for sid, lids in seen_samples.items():
            if len(lids) > 1:
                add(QCIssue(f"dupsample:{im.image_id}:{sid}", LEVEL_WARN, "重複割当",
                            f"{im.image_id} で sample_id「{sid}」が複数レーン（{', '.join(lids)}）に割り当てられています。",
                            "technical replicate として別ロードした場合は、別々の sample_id（同じ bio_rep）にしてください。",
                            im.image_id))
        sizes: dict[str, set] = {}
        for b in im.bands:
            if b.lane_id not in lane_ids:
                add(QCIssue(f"nolane:{im.image_id}:{b.band_id}", LEVEL_WARN, "ROI",
                            f"バンド {b.band_id} の所属レーン（{b.lane_id or '未設定'}）が存在しません。",
                            "バンドを削除して、レーンを選択してから追加し直してください。", im.image_id, [b.band_id]))
            if not b.rect.inside(im.width, im.height):
                add(QCIssue(f"bandout:{im.image_id}:{b.band_id}", LEVEL_WARN, "ROI",
                            f"バンド {b.band_id} の ROI が画像外にはみ出しています（画像内のみ測定）。",
                            "ROI を画像内に移動してください。", im.image_id, [b.band_id]))
            sizes.setdefault(im.band_protein(b), set()).add((b.rect.w, b.rect.h))
        for prot, sz in sizes.items():
            if len(sz) > 1:
                add(QCIssue(f"size:{im.image_id}:{prot}", LEVEL_WARN, "ROI サイズ不一致",
                            f"{im.image_id} の {prot} のバンド ROI の大きさがそろっていません：{sorted(sz)}。",
                            "ROI の大きさが違うと積分値の比較が不公平になります。「ROI サイズを揃える」を使うか、意図的なら理由を記録してください。",
                            im.image_id))
        for b1, b2 in combinations(im.bands, 2):
            if b1.rect.intersects(b2.rect):
                add(QCIssue(f"overlap:{im.image_id}:{b1.band_id}:{b2.band_id}", LEVEL_WARN, "ROI 重複",
                            f"バンド {b1.band_id} と {b2.band_id} の ROI が重なっています。",
                            "同じ画素を2回数えることになります。ROI を小さくするか位置を調整してください。",
                            im.image_id, [b1.band_id, b2.band_id]))
        # 背景 ROI とバンド ROI の交差
        for b in im.bands:
            method = im.band_bg_method(b)
            if method == "local":
                bg_rects = local_background_rects(b.rect, im.bg_gap, im.bg_height, im.width, im.height)
            elif method == "manual_roi":
                bg_rects = im.bg_rois
            else:
                bg_rects = []
            hits = [o.band_id for o in im.bands for r in bg_rects if r.intersects(o.rect)]
            if hits:
                add(QCIssue(f"bgcross:{im.image_id}:{b.band_id}", LEVEL_WARN, "背景 ROI とバンドの交差",
                            f"バンド {b.band_id} の背景領域がバンド ROI（{', '.join(sorted(set(hits)))}）に入り込んでいます。",
                            "背景にバンドの信号が混ざると背景が過大評価されます。背景の間隔（gap）や手動背景 ROI の位置を変えてください。",
                            im.image_id, [b.band_id]))

    # 同一膜の別露光が両方「定量に使う」になっていないか
    groups: dict[tuple, list[str]] = {}
    for im in project.images:
        if im.include and im.membrane_id:
            groups.setdefault((im.membrane_id, im.protein_target), []).append(im.image_id)
    for (mem, prot), iids in groups.items():
        if len(iids) > 1:
            add(QCIssue(f"multiexp:{mem}:{prot}", LEVEL_WARN, "露光の重複",
                        f"膜 {mem} の {prot or '(タンパク質未設定)'} について、複数画像（{', '.join(iids)}）が「定量に使う」になっています。",
                        "同じ膜の異なる露光は独立な反復ではありません。飽和していない露光を1枚選び、他は「定量に使う」を外してください。"))

    if not measurements.empty:
        for _, r in measurements.iterrows():
            band_key = f"{r['image_id']}:{r['band_id']}"
            sat = r["n_low_extreme"] if r["polarity"] == POLARITY_DARK else r["n_high_extreme"]
            if sat > 0:
                add(QCIssue(f"bandsat:{band_key}", LEVEL_WARN, "飽和候補（バンド内）",
                            f"バンド {band_key} の ROI 内に端値（{'0' if r['polarity'] == POLARITY_DARK else '最大値'}）の画素が {int(sat)} 個あります。"
                            + ("" if r["image_included"] else "（定量に使わない画像）"),
                            "バンドが飽和している疑いがあります。積分値は真の量を過小評価します。短い露光の画像で測定し直してください。",
                            r["image_id"], [r["band_id"]]))
            if np.isfinite(r["corrected"]) and r["corrected"] <= 0:
                add(QCIssue(f"neg:{band_key}", LEVEL_NOTE, "負の補正値",
                            f"バンド {band_key} の背景補正後積分値が {r['corrected']:.4g}（0 以下）です。",
                            "バンドが検出限界以下か、背景の選び方・極性設定が不適切な可能性があります。0 に丸めずそのまま記録しています。",
                            r["image_id"], [r["band_id"]]))
            if not np.isfinite(r["bg_value"]):
                add(QCIssue(f"nobg:{band_key}", LEVEL_ERROR, "背景未定義",
                            f"バンド {band_key} の背景を推定できませんでした。{r['warnings']}",
                            "背景 ROI を追加するか、背景推定法を変更してください。", r["image_id"], [r["band_id"]]))
    return issues


def rect_str(r: Rect) -> str:
    return f"({r.x},{r.y},{r.w},{r.h})"
