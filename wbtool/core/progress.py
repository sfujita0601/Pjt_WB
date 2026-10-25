"""解析の進捗（各段階の状態・未入力項目・解析できない理由）。GUI 非依存。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..models.project import QC_PENDING, Project
from .normalize import NormalizationResult

DONE, WARN, TODO, BLOCKED = "done", "warn", "todo", "blocked"
ICONS = {DONE: "✓", WARN: "⚠", TODO: "・", BLOCKED: "✗"}


@dataclass
class Step:
    key: str            # 対応する画面（main_window の pages のキー）
    title: str
    status: str
    details: list[str] = field(default_factory=list)


def compute_progress(project: Project, measurements: pd.DataFrame, norm: NormalizationResult,
                     qc_issues: list, stats_ok: bool, stats_reason: str = "") -> list[Step]:
    steps: list[Step] = []
    ims = project.images
    # 1. 画像
    d = []
    if not ims:
        steps.append(Step("images", "画像の登録", TODO, ["画像がありません。［画像・試料情報］で追加してください。"]))
    else:
        for im in ims:
            miss = [n for n, v in (("タンパク質", im.protein_target), ("膜ID", im.membrane_id), ("露光ID", im.exposure_id)) if not v]
            if miss:
                d.append(f"{im.image_id}：{'・'.join(miss)} が未入力")
        no_prot = any(not im.protein_target for im in ims)
        steps.append(Step("images", "画像の登録", WARN if d else DONE, d or [f"{len(ims)} 枚"]))
        if no_prot:
            steps[-1].status = TODO
    # 2. レーン・バンド
    d = []
    used = [im for im in ims if im.include]
    for im in used:
        if not im.lanes:
            d.append(f"{im.image_id}：レーンがありません")
        elif not im.bands:
            d.append(f"{im.image_id}：バンド ROI がありません")
    n_b = sum(len(im.bands) for im in used)
    steps.append(Step("roi", "レーン・バンドの ROI", TODO if (not n_b or d) else DONE, d or [f"定量に使う画像のバンド {n_b} 個"]))
    # 3. 試料の対応付け
    d = []
    sids = {s.sample_id for s in project.samples}
    for im in used:
        no = [l.lane_id for l in im.lanes if not l.sample_id]
        unk = [l.lane_id for l in im.lanes if l.sample_id and l.sample_id not in sids]
        if no:
            d.append(f"{im.image_id}：試料ID 未割当のレーン {', '.join(no)}")
        if unk:
            d.append(f"{im.image_id}：試料表に無い試料ID のレーン {', '.join(unk)}")
    steps.append(Step("roi", "レーンと試料の対応付け", TODO if d else (DONE if n_b else TODO), d or ["すべてのレーンに試料IDあり"]))
    # 4. 試料情報
    d = []
    for s in project.samples:
        miss = [n for n, v in (("群", s.group), ("生物学的反復", s.bio_rep)) if not v]
        if miss:
            d.append(f"{s.sample_id}：{'・'.join(miss)} が未入力")
    steps.append(Step("images", "試料情報（群・反復）", TODO if (d or not project.samples) else DONE,
                      d or ([f"{len(project.samples)} 試料"] if project.samples else ["試料表が空です"])))
    # 5. QC
    pending = [q for q in qc_issues if project.qc_decisions.get(q.key) is None or project.qc_decisions[q.key].decision == QC_PENDING]
    errors = [q for q in qc_issues if q.level == "エラー"]
    st = BLOCKED if errors else (WARN if pending else DONE)
    det = [f"エラー {len(errors)} 件"] if errors else []
    det += [f"未確認の警告 {len(pending)} 件（判断と理由を記録してください）"] if pending else []
    steps.append(Step("qc", "QC（判断の記録）", st, det or ["すべて判断済み"]))
    # 6. 正規化
    s = project.settings
    if not s.numerator_protein or not s.denominator_protein:
        steps.append(Step("norm", "正規化", TODO, ["分子と分母のタンパク質を選んでください"]))
    elif norm.blocking:
        steps.append(Step("norm", "正規化", BLOCKED, list(norm.blocking)))
    else:
        n_ok = int(norm.bio_table["ratio"].notna().sum()) if len(norm.bio_table) else 0
        bad = int((norm.sample_table["status"] != "OK").sum()) if len(norm.sample_table) else 0
        det = [f"生物学的反復 {n_ok} 個の比を計算"]
        if bad:
            det.append(f"比を計算できない試料 {bad} 個（［正規化］の状態列）")
        if not s.control_group:
            det.append("対照群が未選択（fold を計算できません）")
        steps.append(Step("norm", "正規化", TODO if n_ok == 0 else (WARN if (bad or not s.control_group) else DONE), det))
    # 7. 統計
    steps.append(Step("stats", "統計", DONE if stats_ok else TODO, ["設定どおりに検定できます"] if stats_ok else
                      [stats_reason or "［統計］で比較する群と手法を選んでください"]))
    # 8. Figure
    has = len(norm.bio_table) and norm.bio_table["ratio"].notna().any() and not norm.blocking
    steps.append(Step("figure", "Figure", DONE if has else TODO, ["作図できます"] if has else ["正規化が完了すると作図できます"]))
    return steps
