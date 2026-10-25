"""正規化（分子タンパク質 / 分母タンパク質）、fold to control、log2。GUI 非依存。

計算の段階（画面とドキュメントに表示する順序と一致させること）
  ① sample_id ごとに  比 R = S(分子) / S(分母)          … 同じ sample_id のバンド同士
  ② 同一 (group, bio_rep) 内の technical replicate の R を算術平均 → 生物学的反復の比
  ③ fold = (② の比) / (対照群の ② の算術平均)            … 表示用の相対値
  ④ （選択時）log2_ratio = log2(② の比)                   … ② が正の値のときのみ
     log2_fold = log2_ratio − (対照群の log2_ratio の平均)、fold_geo = 2^log2_fold
     … 対照群の幾何平均を 1 とした相対値

検定には ② の比または ④ の log2_ratio を使う。③ の fold は ② を定数で割っただけなので、
検定結果（p 値）は ② と同じになる。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

STEP_DESCRIPTION = (
    "① sample_id ごとに 比 = S(分子) ÷ S(分母)\n"
    "② 同じ群・同じ生物学的反復（bio_rep）内の技術的反復（tech_rep）の比を算術平均（技術的反復は独立な n に数えない）\n"
    "③ 対照群を 1 とした相対値 fold = ② の比 ÷ 対照群の ② の平均（表示用）\n"
    "④ log2 を選んだ場合：log2_ratio = log2(② の比)（正の値のみ）。"
    "log2_fold = log2_ratio − 対照群の log2_ratio の平均（対照群の幾何平均を 1 とした相対値の log2）"
)

SAMPLE_COLS = ["sample_id", "group", "bio_rep", "tech_rep", "subject_id", "batch_id",
               "numerator", "denominator", "ratio", "fold",
               "num_band", "num_membrane", "num_exposure", "num_bit_depth",
               "den_band", "den_membrane", "den_exposure", "den_bit_depth",
               "status", "caution"]
BIO_COLS = ["group", "bio_rep", "subject_id", "batch_id", "n_tech", "ratio", "fold",
            "log2_ratio", "log2_fold", "fold_geo", "log2_status", "sample_ids"]


@dataclass
class NormalizationResult:
    sample_table: pd.DataFrame     # sample_id ごと
    bio_table: pd.DataFrame        # (group, bio_rep) ごと
    control_mean: float            # 対照群の比の算術平均（③）
    control_log2_mean: float = float("nan")   # 対照群の log2_ratio の平均（④）
    issues: list[str] = field(default_factory=list)
    #: 統計・Figure を止めるべき問題（例：8-bit と 16-bit の混在）
    blocking: list[str] = field(default_factory=list)


def _usable(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["image_included"].astype(bool)) & (~df["excluded"].astype(bool))]


def _band_info(row: pd.Series) -> dict:
    return {"band": f"{row['image_id']}/{row['band_id']}", "membrane": str(row.get("membrane_id", "")),
            "exposure": str(row.get("exposure_id", "")), "bit_depth": int(row["bit_depth"]) if "bit_depth" in row and pd.notna(row["bit_depth"]) else 0}


def normalize(measurements: pd.DataFrame, numerator: str, denominator: str,
              control_group: str = "", samples: list | None = None) -> NormalizationResult:
    issues: list[str] = []
    empty = NormalizationResult(pd.DataFrame(columns=SAMPLE_COLS), pd.DataFrame(columns=BIO_COLS), float("nan"),
                                issues=issues)
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

    meta = {s.sample_id: s for s in (samples or [])}
    rows = []
    for sid in dict.fromkeys(df["sample_id"].tolist()):
        sub = df[df["sample_id"] == sid]
        num = sub[sub["protein"] == numerator]
        den = sub[sub["protein"] == denominator]
        m = meta.get(sid)
        row = {c: "" for c in SAMPLE_COLS}
        row.update(sample_id=sid, group=m.group if m else (sub["group"].iloc[0] if len(sub) else ""),
                   bio_rep=m.bio_rep if m else "", tech_rep=m.tech_rep if m else "",
                   subject_id=getattr(m, "subject_id", "") if m else "", batch_id=getattr(m, "batch_id", "") if m else "",
                   numerator=np.nan, denominator=np.nan, ratio=np.nan, fold=np.nan,
                   num_bit_depth=0, den_bit_depth=0)
        status, caution = [], []
        if len(num) > 1 or len(den) > 1:
            which = numerator if len(num) > 1 else denominator
            status.append(f"重複割当：{which} のバンドが同じ sample_id に {max(len(num), len(den))} 個あります"
                          "（別露光の画像は1枚だけ「定量に使う」にしてください）")
        if len(num) == 0:
            status.append(f"{numerator} のバンドがありません")
        if len(den) == 0:
            status.append(f"{denominator} のバンドがありません")
        if len(num) == 1:
            r = num.iloc[0]
            info = _band_info(r)
            row.update(numerator=float(r["corrected"]), num_band=info["band"], num_membrane=info["membrane"],
                       num_exposure=info["exposure"], num_bit_depth=info["bit_depth"])
        if len(den) == 1:
            r = den.iloc[0]
            info = _band_info(r)
            row.update(denominator=float(r["corrected"]), den_band=info["band"], den_membrane=info["membrane"],
                       den_exposure=info["exposure"], den_bit_depth=info["bit_depth"])
        if not status and not np.isfinite(row["numerator"]):
            status.append("分子の S が無効（NaN：背景を推定できない、または ROI が画像外）のため計算しません")
        if not status:
            d = row["denominator"]
            if not np.isfinite(d) or d <= 0:
                status.append(f"分母が 0 以下または無効（{d:.4g}）のため計算しません")
            else:
                row["ratio"] = row["numerator"] / d
                if row["numerator"] <= 0:
                    caution.append("分子が 0 以下（バンドが背景より弱い）")
        if row["num_membrane"] and row["den_membrane"] and row["num_membrane"] != row["den_membrane"]:
            caution.append("分子と分母が別の膜：対応付けはできても、正規化として適切とは限りません")
        if row["num_bit_depth"] and row["den_bit_depth"] and row["num_bit_depth"] != row["den_bit_depth"]:
            caution.append(f"分子 {row['num_bit_depth']}-bit と分母 {row['den_bit_depth']}-bit で階調スケールが異なります")
        row["status"] = "；".join(status) if status else "OK"
        row["caution"] = "；".join(caution)
        rows.append(row)
    st = pd.DataFrame(rows, columns=SAMPLE_COLS)

    bad = st[st["ratio"].isna()]
    if len(bad):
        issues.append(f"{len(bad)} 個の sample_id で比を計算できませんでした（表の「状態」列を参照）。")
    diff_mem = st[st["caution"].str.contains("別の膜", na=False)]
    if len(diff_mem):
        issues.append(f"{len(diff_mem)} 個の試料で分子と分母が別の膜（membrane_id）です。sample_id で対応付けはできますが、"
                      "膜ごとの転写効率・露光の違いは補正されないため、同じ膜の再プローブより前提が弱いことに注意してください。")
    mems = set(st["num_membrane"]) - {""}
    if len(mems) > 1:
        issues.append(
            f"分子の測定が複数の膜（{', '.join(sorted(mems))}）にまたがっています。膜・露光・実験日が異なる値の直接比較は、"
            "共通参照レーン等による膜間正規化（未実装）なしでは保証されません。")

    # 8-bit と 16-bit の混在：同じタンパク質の値を異なる階調スケールで比べることになる
    blocking: list[str] = []
    valid_st = st[st["ratio"].notna()]
    for col, name in (("num_bit_depth", numerator), ("den_bit_depth", denominator)):
        depths = sorted(set(valid_st[col]) - {0})
        if len(depths) > 1:
            blocking.append(f"{name} の値が {' と '.join(f'{d}-bit' for d in depths)} の画像から混在しています。"
                            "階調スケールが異なる値は直接比較できないため、統計と Figure を止めています。"
                            "同じ bit 深度で取り込んだ画像だけを「定量に使う」にしてください。")
    if any(st["caution"].str.contains("階調スケール", na=False)):
        issues.append("一部の試料で分子と分母の bit 深度が異なります。比の絶対値は階調スケールに依存します"
                      "（全試料で同じ組み合わせなら、対照群を 1 とした相対値は比較できます）。")

    # ② 生物学的反復へ集約
    valid = valid_st.copy()
    missing_bio = valid[valid["bio_rep"].astype(str) == ""]
    if len(missing_bio):
        issues.append(f"生物学的反復（bio_rep）が未入力の試料が {len(missing_bio)} 個あり、集計から外しました。")
    valid = valid[valid["bio_rep"].astype(str) != ""]
    if valid.empty:
        bio = pd.DataFrame(columns=BIO_COLS)
    else:
        def uniq(x):
            vals = sorted(set(map(str, x)) - {""})
            return ",".join(vals)
        bio = (valid.groupby(["group", "bio_rep"], sort=False)
               .agg(n_tech=("ratio", "size"), ratio=("ratio", "mean"), subject_id=("subject_id", uniq),
                    batch_id=("batch_id", uniq), sample_ids=("sample_id", lambda x: ",".join(x)))
               .reset_index())
        conflict = bio[bio["subject_id"].str.contains(",")]
        if len(conflict):
            issues.append("同じ生物学的反復の中で subject_id が複数あります：" +
                          ", ".join(f"{r.group}/{r.bio_rep}" for r in conflict.itertuples()) + "。試料表を確認してください。")
        bio["fold"] = np.nan
        pos = bio["ratio"] > 0
        bio["log2_ratio"] = np.where(pos, np.log2(bio["ratio"].where(pos, 1.0)), np.nan)
        bio["log2_status"] = np.where(pos, "OK", "比が 0 以下または欠損のため log2 変換できません（擬似値で置き換えません）")
        bio["log2_fold"] = np.nan
        bio["fold_geo"] = np.nan
        bio = bio[BIO_COLS]

    # ③④ 対照群を基準にした相対値
    control_mean = control_log2 = float("nan")
    if control_group:
        ctrl = bio[bio["group"] == control_group]
        if len(ctrl) == 0:
            issues.append(f"対照群「{control_group}」に有効な生物学的反復がありません。fold は計算しません。")
        else:
            control_mean = float(ctrl["ratio"].mean())
            if not np.isfinite(control_mean) or control_mean <= 0:
                issues.append("対照群の平均比が 0 以下のため fold は計算しません。")
                control_mean = float("nan")
            else:
                bio["fold"] = bio["ratio"] / control_mean
                st["fold"] = st["ratio"] / control_mean
            cl = ctrl["log2_ratio"]
            if cl.notna().all() and len(cl):
                control_log2 = float(cl.mean())
                bio["log2_fold"] = bio["log2_ratio"] - control_log2
                bio["fold_geo"] = np.power(2.0, bio["log2_fold"])
            else:
                issues.append("対照群に log2 変換できない値があるため、log2_fold は計算しません。")
    else:
        issues.append("対照群が未選択のため、対照群を 1 とした相対値（fold）は計算していません。")
    n_nolog = int((bio["log2_status"] != "OK").sum()) if len(bio) else 0
    if n_nolog:
        issues.append(f"{n_nolog} 個の生物学的反復は比が 0 以下または欠損のため log2 変換できません（表の log2_status 列）。")
    return NormalizationResult(st, bio, control_mean, control_log2, issues, blocking)
