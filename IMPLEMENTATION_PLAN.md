# 実装計画（IMPLEMENTATION_PLAN）

作成日：2026-10-25　対象：Western blot 定量・統計・Figure 作成 GUI アプリ（`wbtool`）

## 0. 前提と技術選定

| 項目 | 採用 | 理由 |
|---|---|---|
| 言語 | Python 3.11+（開発は 3.12） | 研究室で保守しやすい。科学計算ライブラリが成熟 |
| GUI | PySide6 6.12 + pyqtgraph 0.14 | ROI のドラッグ・リサイズ・拡大縮小をローカルで高速に扱える |
| 数値計算 | NumPy / SciPy / pandas | 定量・統計を GUI から独立した関数で実装し単体テスト |
| 画像 I/O | tifffile（TIFF）/ Pillow（PNG・JPEG） | 16-bit を 8-bit に縮小せず読み込む |
| 作図 | matplotlib | PNG/SVG/PDF を同一コードで出力 |
| テスト | pytest | 合成画像で「正解が計算できる」テスト |

既存リポジトリには仕様書（`prompt/`）のみでコードは無かったため、新規に構築した。
`app`・`core`・`models` という一般的な名前がほかのパッケージと衝突しないよう、
`wbtool/` パッケージの下に `app/`・`core/`・`models/` を置いた（仕様 G の構成と同じ役割分担）。

## 1. アーキテクチャ

```
wbtool/
├── models/project.py   … 画像・レーン・バンドROI・背景ROI・試料・QC判断・履歴（dataclass、JSON保存）
├── core/               … GUI に依存しない計算（単体テスト対象）
│   ├── imageio.py      … 読込・bit depth・SHA-256
│   ├── quant.py        … 背景補正積分 S = Σ s(p_i − b_i)、プロファイル
│   ├── lanes.py        … レーン候補提示（半自動）
│   ├── qc.py           … 飽和・ROI不整合・重複割当の検知
│   ├── normalize.py    … Target / Loading control、fold to control
│   ├── stats.py        … 記述統計、Welch t、対応 t、効果量、95%CI
│   ├── figure.py       … matplotlib による作図・保存
│   ├── pipeline.py     … プロジェクト全体の測定 → 正規化 → 集計
│   ├── export.py       … CSV/JSON/ROIオーバーレイ出力
│   └── synthetic.py    … 教材用合成画像（正解値つき）
└── app/                … PySide6 GUI（計算は core を呼ぶだけ）
```

計算結果は「プロジェクト（入力と設定）」から毎回再計算する。プロジェクトファイルには最後の測定値も保存し、
再読込時に再計算値と一致するか照合する（受け入れテスト 9）。

## 2. MVP（このセッションで実装）

- [x] プロジェクト作成・保存・再読込（JSON、原画像はパス＋SHA-256 で参照し複製・改変しない）
- [x] 画像登録・メタデータ編集、試料表（sample_id / group / biological / technical replicate）、CSV 取込・出力
- [x] 画像移動時の再リンク、ハッシュ不一致警告
- [x] 画像表示（パン・ズーム）、原画像／反転表示／自動コントラストの切替（測定値に影響しない）
- [x] レーン候補提示（列プロファイルのピーク＋等間隔フォールバック）、ROI の追加・移動・リサイズ・削除
- [x] ROI サイズの一括統一、個別修正、変更履歴
- [x] レーンプロファイル表示（バンド範囲・背景レベル連動）、参考としてプロファイル面積（直線ベースライン）
- [x] 背景補正積分（背景なし／手動背景ROI平均／同一レーン近傍背景）、極性指定、負値を丸めない
- [x] QC 警告と判断記録（採用／除外／再撮影推奨、理由）
- [x] 正規化（任意の分子／分母タンパク質、fold to control、計算段階の表示）
- [x] 統計（記述統計、Welch t、対応 t、効果量、95%CI、前提と注意の表示）
- [x] Figure（点＋平均±SD、棒＋点、箱ひげ＋点、スタイル変更、PNG/SVG/PDF 保存）
- [x] 測定 CSV・設定 JSON・ROI 一覧・ROI オーバーレイ画像の出力
- [x] Undo / Redo（プロジェクト状態のスナップショット方式）
- [x] 学習モード（7 ページ、統一構成）と比較・体験画面、内蔵マニュアル
- [x] 合成画像による単体テスト、サンプルデータ、README / docs

## 3. Phase 2（MVP 検証後）

- 自動バンド候補検出の高度化（検証データで精度評価してから）
- 一元配置 ANOVA・多重比較・二要因 ANOVA・混合効果モデル（現在は GUI 上で選択不可）
- 共通参照レーン／ブリッジサンプルによる膜間正規化
- 希釈系列・複数露光による線形範囲の確認 QC
- 実画像を用いた Fiji/ImageJ との照合結果の蓄積（`docs/validation_imagej.md` の手順で実施）
- バンド画像トリミングと複数パネルを組み合わせた Figure レイアウト
- ROI ごとの回転、多角形 ROI

## 4. Phase 3（必要に応じて）

- macOS/Windows 向け単体配布（PyInstaller 等）
- 学習進捗・小問の解答記録
- 研究室カスタムプロトコルテンプレート
- 画像処理手法（rolling ball 等）の比較教材。ML によるブラックボックス定量は採用しない
