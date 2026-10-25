# Fiji/ImageJ との照合プロトコル

対象：本アプリ（`wbtool`）の定量値を、Fiji/ImageJ の測定値と突き合わせて確認する人
関連：`docs/theory_ja.md`（式の定義）、`wbtool/core/quant.py`（実装）

## 1. 目的と考え方

本アプリの矩形 ROI 積分は、ImageJ の **Analyze > Measure** と同じく「ROI 内の画素値の和と平均」を基本にしています。そのため、

- **同じ画像（同じ画素値）**
- **同じ ROI 座標**
- **同じ極性**
- **同じ背景値**

で比べれば、補正前の値（画素数・画素値の和・平均）は**一致するはず**です。一致しなければ、どちらかの条件がそろっていないか、どちらかに不具合があります。

一方、ImageJ の **Gel Analyzer（Analyze > Gels）のピーク面積**は測定の定義が違うので、本アプリの S と直接比べてはいけません（第 7 節）。数値が違うことをバグとみなさないでください。

照合結果の蓄積（実画像 10〜20 枚での比較）は Phase 2 の作業です。この文書は、その手順と、合成画像で期待される値を示します。

## 2. 対応する値

| 本アプリ（測定 CSV の列） | ImageJ（Results の列） | 一致の条件 |
|---|---|---|
| `n_pixels`（ROI の画素数 N） | **Area** | 空間較正なし（単位が pixel）のとき一致。較正があると Area は µm² や inch² になる |
| `raw_sum`（Σ p_i） | **RawIntDen**（ROI 内の画素値の総和） | 較正の有無に関係なく一致するはず |
| `raw_mean`（raw_sum / N） | **Mean** | 濃度較正（Analyze > Calibrate）がないとき一致 |
| `bg_value`（b） | 背景 ROI の Mean（複数なら画素数で重み付け、下記） | 背景 ROI の座標がそろっていること |
| `corrected`（S） | ImageJ に直接対応する列はない。下の式で手計算する | — |

### IntDen と RawIntDen の違い

- **RawIntDen**：ROI 内の画素値の総和。本アプリの `raw_sum` に対応します。
- **IntDen**：`Area × Mean`。空間較正がなければ Area = 画素数なので RawIntDen と同じ値になりますが、空間較正があると Area が較正単位（例：inch²）になり、IntDen も較正単位の値になります。
- スキャナーで作った TIFF には解像度（dpi）が記録されていることが多く、ImageJ で開くと自動的に inch などの単位が設定されることがあります。**照合には RawIntDen を使ってください。** Area を画素数で比べたい場合は、Image > Properties で単位が pixel になっていることを確認するか、Analyze > Set Scale で較正を外します（マクロ例を参照）。

### 背景補正後の S を ImageJ の値から計算する

本アプリの測定 CSV には、背景推定に使った矩形が `bg_rects` 列に `x,y,w,h;x,y,w,h` の形式で出力されます。ImageJ でも、この背景矩形を ROI として測ります。

1. バンド ROI を測る → RawIntDen_band、Area_band（= N）
2. 背景矩形をそれぞれ測る → RawIntDen_bg1, Area_bg1, RawIntDen_bg2, Area_bg2, …
3. 背景値は**全画素の平均**（画素数で重み付け）：
   `b = (RawIntDen_bg1 + RawIntDen_bg2 + …) / (Area_bg1 + Area_bg2 + …)`
   上下の帯の面積が同じなら Mean の単純平均と同じですが、片方が画像端でクリップされて面積が違う場合は単純平均と一致しません。
4. `S = s × (RawIntDen_band − N × b)`、s は暗いバンドなら −1、明るいバンドなら +1。

背景法ごとの背景矩形：

| 背景法 | 背景の取り方（ImageJ で測る領域） |
|---|---|
| 近傍上下帯 `local` | バンド ROI と**同じ x・幅**で、真上 `makeRectangle(x, y − gap − height, w, height)` と真下 `makeRectangle(x, y + h + gap, w, height)`。初期値 gap = 3、height = 5（画像内にクリップされる） |
| 手動背景 ROI `manual_roi` | 画像に設定した背景 ROI すべて（ROI 一覧 `roi_list.csv` と `bg_rects` 列に座標あり） |
| 補正なし `none` | 測る必要なし。暗いバンドは b = 255（8-bit）または 65535（16-bit）、明るいバンドは b = 0 |

## 3. 条件をそろえる

### 3.1 ROI 座標

- 本アプリの ROI は `Rect(x, y, w, h)`：原画像の画素座標、**左上原点**、x = 列、y = 行、**整数**です。含まれる画素は列 x〜x+w−1、行 y〜y+h−1 です。
- ImageJ の `makeRectangle(x, y, w, h)` も左上原点の整数画素座標で、同じ画素の集合を選びます。測定 CSV の `roi_x, roi_y, roi_w, roi_h` をそのまま入れてください。
- 画面上でドラッグして ROI を描くと 1 画素ずれやすいので、**必ずマクロか Edit > Selection > Specify… で数値指定**します。
- 本アプリで ROI が画像外にはみ出している場合（`roi_clipped = True`）、本アプリは画像内の部分だけを測ります。照合には画像内に収まった ROI を使ってください。

### 3.2 画素値（同じ画像を使う）

- 照合には、本アプリに読み込んだ**同じファイル**を ImageJ で開きます。ImageJ 上で 8-bit 変換、コントラスト調整の「Apply」、背景除去（Subtract Background）などを行うと画素値が変わります。
- **Image > Lookup Tables > Invert LUT** は表示用の色の対応を変えるだけで、**画素値は変わりません**。Measure の値も変わりません（本アプリの「表示の反転」と同じ）。
- **Edit > Invert** は**画素値そのものを書き換えます**（8-bit では p → 255 − p）。16-bit 画像では、反転の基準が 0〜65535 ではなく画像や表示範囲の最小・最大になる場合があり、ImageJ のバージョンによって挙動が違うことがあるので、照合では使わないでください。反転画像で比べたい場合は、本アプリでも同じ反転済みファイルを読み込み、極性を `bright` にします。
- 画像を開いたときに ImageJ が自動で LUT や画素値を反転することがあります（TIFF の WhiteIsZero 指定など）。**数点の画素値を ImageJ のポインター（ステータスバーの value）で確認し、本アプリの値と一致すること**を最初に確かめてください。
- **16-bit 画像**：ImageJ は 16-bit をそのまま扱い、Measure も元の値（0〜65535）で計算します。表示の最小・最大（Brightness/Contrast）を変えても、Apply を押さなければ画素値は変わりません。Image > Type > 8-bit に変換すると、表示範囲に合わせて値が縮小されるので、照合前に変換してはいけません。
- **カラー画像**：本アプリは BT.601 の重み（0.299R + 0.587G + 0.114B）でグレースケールにします。ImageJ の RGB → 8-bit 変換は、初期設定では重みなしの (R+G+B)/3 で、Edit > Options > Conversions の「Weighted RGB conversions」を有効にすると重み付きになります。丸め方も一致する保証はないので、**照合にはグレースケール画像を使ってください。**

### 3.3 極性と背景

- 極性は ImageJ 側には存在しない設定です。ImageJ では RawIntDen と Mean（生の画素値）を測り、極性の符号 s は手計算で掛けます。
- 背景値は、本アプリが出力した背景矩形を ImageJ で測って求めます（第 2 節）。ImageJ の Subtract Background（rolling ball）は本アプリの背景法とは別物なので、照合では使いません。

### 3.4 Set Measurements の設定

Analyze > Set Measurements… で少なくとも次を選びます。

- **Area**
- **Mean gray value**
- **Integrated density**（RawIntDen と IntDen が出力される）
- Min & max gray value（端値＝飽和の疑いの確認に便利）
- Display label（どの画像の値か分かるように）
- Decimal places：3 以上。値が大きいと Results に指数表記で表示されることがあるので、桁を確認してから記録します。

## 4. ImageJ マクロの例

Plugins > New > Macro に貼り付けて実行します（ImageJ 1.x マクロ言語）。

```javascript
// 1) 空間較正を外す（Area を画素数にする）
run("Set Scale...", "distance=0 known=0 unit=pixel");
// 2) 測定項目
run("Set Measurements...", "area mean min integrated display redirect=None decimal=3");

// 3) バンド ROI（examples/synthetic_simple_8bit.png のバンド 1）
makeRectangle(18, 45, 22, 10);
run("Measure");

// 4) 近傍背景（gap=3, height=5、バンドと同じ x・幅）
makeRectangle(18, 37, 22, 5);   // 上：y = 45 - 3 - 5
run("Measure");
makeRectangle(18, 58, 22, 5);   // 下：y = 45 + 10 + 3
run("Measure");

run("Select None");
```

複数のバンドを測る場合は、測定 CSV から `roi_x, roi_y, roi_w, roi_h` を写して `makeRectangle` の行を増やします。

## 5. 記録用の表（テンプレート）

画像 1 枚ごとに次の表を作り、照合結果を残します。

| 項目 | 記入 |
|---|---|
| 画像ファイル名 | |
| SHA-256（本アプリの記録） | |
| bit depth / dtype | |
| ImageJ / Fiji のバージョン | |
| 本アプリのバージョン | |
| 極性（dark / bright） | |
| 背景法とパラメータ（gap, height, 手動背景 ROI） | |
| 空間較正を外したか | |
| 照合日・照合者 | |

| band_id | ROI (x,y,w,h) | n_pixels | ImageJ Area | raw_sum | ImageJ RawIntDen | raw_mean | ImageJ Mean | 背景矩形 | b（本アプリ） | b（ImageJ から計算） | S（本アプリ） | S（ImageJ から計算） | 差 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | | | |

## 6. 許容誤差の考え方

- 画素値は整数なので、同じ画素集合なら **Area（画素数）と RawIntDen（整数の和）は完全に一致する**はずです。本アプリは倍精度浮動小数点で和を取りますが、16-bit 画像の数百万画素程度の和は 2^53 より十分小さく、丸め誤差は生じません。
- Mean、b、S は割り算や掛け算を含むので、**表示桁や浮動小数点の丸め程度**（相対誤差 1e-9 程度、ImageJ の表示桁が 3 桁ならその桁まで）の差は許容します。
- 画素数や和が 1 画素ぶんでもずれる場合は、ROI 座標（1 画素ずれ）、画像外へのはみ出し、画像の読み込み方（自動反転、カラー変換、別のファイル）を疑います。**差が出たら許容範囲を広げるのではなく、原因を特定して記録してください。**

## 7. Gel Analyzer のピーク面積とは比べない

ImageJ の Analyze > Gels（Gel Analyzer）は、

- レーンの縦方向プロファイル（レーン幅方向に平均した値）を作り、
- ユーザーが手で引いた直線でピークを閉じ、
- 魔法の杖ツールでピークの面積を測ります。
- Gel Analyzer Options で「Uncalibrated OD」を有効にすると、画素値が OD 風の値に変換されてから計算されます。

これは**矩形 ROI の背景補正積分 S とは定義が違います**（ベースラインの位置、プロファイルの平均方向、単位、OD 変換の有無）。同じバンドでも一致しないのが普通です。

本アプリの**プロファイル面積は参考値**で、定義は次のとおりです。

- レーンプロファイル：レーン ROI 内の各行 y について、列方向の平均画素値 v_y。
- 面積：指定範囲 y_start〜y_end−1 で、範囲の両端の 2 点を結ぶ直線ベースライン base_y を引き、`Σ_y s · (v_y − base_y)`。単位は「行平均画素値 × 行数」。

Gel Analyzer と考え方は近いものの、ベースラインの引き方などが同じとは限らないので、数値の一致は期待しません。照合の対象は**矩形 ROI の Area / RawIntDen / Mean と、それから計算した S**に限ります。

## 8. 合成画像での期待値

`examples/synthetic_simple_8bit.png`（教材用合成データ、実験由来ではありません）を使います。

- 190 × 100 画素、8-bit グレースケール PNG、解像度情報なし（ImageJ で開いても空間較正は付かないはず）。
- 背景はすべて 220、バンドは矩形で、背景からの深さが 40 / 80 / 120 / 160（画素値 180 / 140 / 100 / 60）。ノイズ・ぼかしなし。
- 極性：暗いバンド（dark）。
- バンド矩形は `examples/synthetic_truth.json` の `simple_8bit.band_rects_xywh`。

以下の値は、本アプリの関数（`load_image`、`estimate_background`、`measure_band`）で計算したものです。

| バンド | makeRectangle(x,y,w,h) | Area = n_pixels | RawIntDen = raw_sum | Mean = raw_mean | 近傍背景の矩形（上 / 下） | b | S（近傍背景） | S（補正なし、b = 255） |
|---|---|---|---|---|---|---|---|---|
| 1 | (18, 45, 22, 10) | 220 | 39,600 | 180 | (18,37,22,5) / (18,58,22,5) | 220 | 8,800 | 16,500 |
| 2 | (62, 45, 22, 10) | 220 | 30,800 | 140 | (62,37,22,5) / (62,58,22,5) | 220 | 17,600 | 25,300 |
| 3 | (106, 45, 22, 10) | 220 | 22,000 | 100 | (106,37,22,5) / (106,58,22,5) | 220 | 26,400 | 34,100 |
| 4 | (150, 45, 22, 10) | 220 | 13,200 | 60 | (150,37,22,5) / (150,58,22,5) | 220 | 35,200 | 42,900 |

- 近傍背景の各帯は 22 × 5 = 110 画素、2 本で 220 画素（`bg_n_pixels`）、RawIntDen はそれぞれ 24,200、Mean は 220 です。
- 近傍背景での S は `synthetic_truth.json` の `true_S_exact_background`（8800, 17600, 26400, 35200）と一致します。
- ImageJ では、各バンドで Area = 220、RawIntDen が上の表の値と**完全に一致**すること、IntDen も（較正がなければ）同じ値になることを確認します。
- 確認の例（バンド 1）：S = −1 × (39,600 − 220 × 220) = −1 × (39,600 − 48,400) = 8,800。

16-bit の合成画像（`examples/synthetic_*_16bit.tif`）はノイズとぼかしを含むので、期待値は本アプリの測定 CSV の値を使い、同じ手順で照合します。

## 参考

- ImageJ Analyze メニュー（Measure、Set Measurements、Gels）：<https://imagej.net/ij/docs/menus/analyze>
- ImageJ User Guide：<https://imagej.net/ij/docs/guide/user-guide.pdf>
