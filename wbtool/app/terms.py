"""学生向け画面の用語（日本語中心、初出に英語・内部名を併記）。内部の変数名・出力キーは英語のまま。"""

TERMS = {
    "sample_id": "試料ID（sample_id）",
    "group": "群（group）",
    "bio_rep": "生物学的反復（bio_rep）",
    "tech_rep": "技術的反復（tech_rep）",
    "subject_id": "実験単位ID（subject_id）",
    "batch_id": "バッチID（batch_id）",
    "membrane_id": "膜ID（membrane_id）",
    "exposure_id": "露光ID（exposure_id）",
    "image_id": "画像ID（image_id）",
    "lane_id": "レーン",
    "band_id": "バンド",
    "protein": "タンパク質",
    "fold": "対照群を1とした相対値（fold）",
    "ratio": "正規化比（ratio）",
    "log2_ratio": "log2（正規化比）",
    "log2_fold": "log2（fold、対照群の幾何平均=0）",
    "fold_geo": "対照群の幾何平均を1とした相対値",
    "n_tech": "技術的反復の数",
    "numerator": "S（分子）",
    "denominator": "S（分母）",
    "status": "状態",
    "caution": "注意",
    "log2_status": "log2 変換",
    "sample_ids": "含まれる試料ID",
    "num_band": "分子のバンド",
    "num_membrane": "分子の膜",
    "num_exposure": "分子の露光",
    "num_bit_depth": "分子の bit",
    "den_band": "分母のバンド",
    "den_membrane": "分母の膜",
    "den_exposure": "分母の露光",
    "den_bit_depth": "分母の bit",
}

#: 試料情報入力欄の説明（画面に表示）
SAMPLE_FIELD_HELP = [
    ("試料ID（sample_id）", "レーンに流した試料1つ1つの名前。画像が別でも同じ試料なら同じ ID。", "S1, S2, …"),
    ("群（group）", "比べたい条件。", "Control, DrugA"),
    ("生物学的反復（bio_rep）", "独立に用意した個体・培養・動物など。統計の n になる単位。群の中で一意。", "C1, C2, T1"),
    ("技術的反復（tech_rep）", "同じ試料を複数レーンに流した繰り返し。n には数えず平均する。", "1, 2"),
    ("実験単位ID（subject_id）", "同じ個体・同じ試料を複数の条件で測ったときだけ、条件をまたいで同じ ID にする。"
     "対応のある検定に必要。同じ日・同じブロットというだけでは同じ ID にしない。", "Mouse01（処理前・処理後で共通）"),
    ("バッチID（batch_id）", "実験日・ブロットなどのまとまり（記録用）。", "2026-10-25"),
]
