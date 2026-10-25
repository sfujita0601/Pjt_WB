"""正規化（分子タンパク質 / 分母タンパク質）と fold to control。GUI 非依存。

計算の段階（画面とドキュメントに表示する順序と一致させること）
  ① sample_id ごとに  比 R = S(分子) / S(分母)          … 同じ sample_id のバンド同士
  ② 同一 (group, bio_rep) 内の technical replicate の R を算術平均 → biological replicate の値
  ③ fold = (② の値) / (Control 群の ② の値の平均)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

STEP_DESCRIPTION = (
    "① sample_id ごとに 比 = S(分子) ÷ S(分母)\n"
    "② 同じ群・同じ biological replicate 内の technical replicate の比を平均（technical は独立な n に数えない）\n"
    "③ fold to control = ② の値 ÷ Control 群の ② の平均"
)


@dataclass
class NormalizationResult:
    sample_table: pd.DataFrame     # sample_id ごと
    bio_table: pd.DataFrame        # (group, bio_rep) ごと
    control_mean: float
    issues: list[str] = field(default_factory=list)


def _usable(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["image_included"].astype(bool)) & (~df["excluded"].astype(bool))]


def normalize(measurements: pd.DataFrame, numerator: str, denominator: str,
              control_group: str = "", samples: list | None = None) -> NormalizationResult:
    issues: list[str] = []
    sample_cols = ["sample_id", "group", "bio_rep", "tech_rep", "numerator", "denominator",
                   "num_band", "den_band", "num_membrane", "den_membrane", "ratio", "fold", "status"]
    bio_cols = ["group", "bio_rep", "n_tech", "ratio", "fold", "sample_ids"]
    empty = NormalizationResult(pd.DataFrame(columns=sample_cols), pd.DataFrame(columns=bio_cols), float("nan"), issues)
    if not numerator or not denominator:
        issues.append("分子と分母のタンパク質を選択してください。")
        return empty
    if numerator == denominator:
        issues.append("分子と分母に同じタンパク質が選ばれています。")
        return empty
    df = _usable(measurements)
    if df.empty:
        issues.append("定量に使えるバンドがありません（画像の「定量に使う」と除外設定を確認してください）。")
        return empty

    no_sample = df[df["sample_id"].astype(str) == ""]
    if len(no_sample):
        issues.append(f"sample_id が割り当てられていないバンドが {len(no_sample)} 個あり、正規化から外しました。")
    df = df[df["sample_id"].astype(str) != ""]

    meta = {}
    for s in samples or []:
        meta[s.sample_id] = s
    sample_ids = list(dict.fromkeys(df["sample_id"].tolist()))
    rows = []
    for sid in sample_ids:
        sub = df[df["sample_id"] == sid]
        num = sub[sub["protein"] == numerator]
        den = sub[sub["protein"] == denominator]
        m = meta.get(sid)
        group = m.group if m else (sub["group"].iloc[0] if len(sub) else "")
        row = {"sample_id": sid, "group": group, "bio_rep": m.bio_rep if m else "", "tech_rep": m.tech_rep if m else "",
               "numerator": np.nan, "denominator": np.nan, "num_band": "", "den_band": "",
               "num_membrane": "", "den_membrane": "", "ratio": np.nan, "fold": np.nan, "status": "OK"}
        status = []
        if len(num) > 1 or len(den) > 1:
            which = numerator if len(num) > 1 else denominator
            status.append(f"重複割当：{which} のバンドが同じ sample_id に {max(len(num), len(den))} 個あります（別露光の画像は1枚だけ「定量に使う」にしてください）")
        if len(num) == 0:
            status.append(f"{numerator} のバンドがありません")
        if len(den) == 0:
            status.append(f"{denominator} のバンドがありません")
        if len(num) == 1:
            row.update(numerator=float(num["corrected"].iloc[0]),
                       num_band=f"{num['image_id'].iloc[0]}/{num['band_id'].iloc[0]}",
                       num_membrane=str(num["membrane_id"].iloc[0]))
        if len(den) == 1:
            row.update(denominator=float(den["corrected"].iloc[0]),
                       den_band=f"{den['image_id'].iloc[0]}/{den['band_id'].iloc[0]}",
                       den_membrane=str(den["membrane_id"].iloc[0]))
        if not status:
            d = row["denominator"]
            if not np.isfinite(d) or d <= 0:
                status.append(f"分母が 0 以下または無効（{d:.4g}）のため計算しません")
            else:
                row["ratio"] = row["numerator"] / d
                if row["numerator"] <= 0:
                    status.append("注意：分子が 0 以下（バンドが背景より弱い）")
        if status:
            row["status"] = "；".join(status)
        rows.append(row)
    st = pd.DataFrame(rows, columns=sample_cols)
    bad = st[st["ratio"].isna()]
    if len(bad):
        issues.append(f"{len(bad)} 個の sample_id で比を計算できませんでした（表の status 列を参照）。")
    mem_pairs = st[(st["num_membrane"] != "") & (st["den_membrane"] != "") & (st["num_membrane"] != st["den_membrane"])]
    if len(mem_pairs):
        issues.append(f"{len(mem_pairs)} 個の試料で分子と分母が別の膜（membrane_id）です。同じ膜の再プローブより前提が弱いことに注意してください。")
    mems = set(st["num_membrane"].dropna()) - {""}
    if len(mems) > 1:
        issues.append(
            f"分子の測定が複数の膜（{', '.join(sorted(mems))}）にまたがっています。膜・露光が異なる値の直接比較は、"
            "共通参照レーン等による膜間正規化（Phase 2、未実装）なしでは保証されません。"
        )

    # ② biological replicate へ集約
    valid = st[st["ratio"].notna()].copy()
    missing_bio = valid[valid["bio_rep"].astype(str) == ""]
    if len(missing_bio):
        issues.append(f"bio_rep（biological replicate ID）が未入力の試料が {len(missing_bio)} 個あり、集計から外しました。")
    valid = valid[valid["bio_rep"].astype(str) != ""]
    if valid.empty:
        bio = pd.DataFrame(columns=bio_cols)
    else:
        bio = (valid.groupby(["group", "bio_rep"], sort=False)
               .agg(n_tech=("ratio", "size"), ratio=("ratio", "mean"),
                    sample_ids=("sample_id", lambda x: ",".join(x)))
               .reset_index())
        bio["fold"] = np.nan
        bio = bio[["group", "bio_rep", "n_tech", "ratio", "fold", "sample_ids"]]

    # ③ fold to control
    control_mean = float("nan")
    if control_group:
        ctrl = bio[bio["group"] == control_group]["ratio"]
        if len(ctrl) == 0:
            issues.append(f"Control 群「{control_group}」に有効な biological replicate がありません。fold は計算しません。")
        else:
            control_mean = float(ctrl.mean())
            if not np.isfinite(control_mean) or control_mean <= 0:
                issues.append("Control 群の平均比が 0 以下のため fold は計算しません。")
                control_mean = float("nan")
            else:
                bio["fold"] = bio["ratio"] / control_mean
                st["fold"] = st["ratio"] / control_mean
    else:
        issues.append("Control 群が未選択のため fold to control は計算していません。")
    return NormalizationResult(st, bio, control_mean, issues)
