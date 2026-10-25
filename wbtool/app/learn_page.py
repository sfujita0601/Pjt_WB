"""［学習・ヘルプ］画面：教材 7 ページ、比較・体験、操作マニュアル。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGraphicsRectItem, QHBoxLayout, QLabel, QListWidget, QSlider, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core.quant import estimate_background, measure_band
from ..core.synthetic import make_blot
from ..models.project import BG_LABELS, BG_LOCAL, BG_MANUAL, BG_NONE, Rect
from .widgets import fmt, page_header

LESSON_DIR = Path(__file__).resolve().parents[1] / "resources" / "lessons"
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
LESSONS = [
    ("01_pixel_values.md", "01 バンド濃度と画素値"),
    ("02_roi_profile.md", "02 ROI とレーンプロファイル"),
    ("03_background.md", "03 背景補正"),
    ("04_loading_control.md", "04 ローディングコントロールの意味"),
    ("05_replicates_stats.md", "05 反復と統計解析"),
    ("06_saturation_limits.md", "06 過露光・飽和・スキャナーの限界"),
    ("07_records_figures.md", "07 実験記録と Figure 作成"),
]
DOCS = [
    ("user_guide_ja.md", "操作マニュアル（最短手順・よくある間違い）"),
    ("theory_ja.md", "理論：積分・背景・正規化・統計"),
    ("protocol_ja.md", "推奨プロトコル（案）：スキャン〜報告"),
    ("validation_imagej.md", "Fiji/ImageJ との照合手順"),
]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return f"# 見つかりません\n\n`{path}` を読み込めませんでした。アプリのフォルダー構成を確認してください。"


class CompareWidget(QWidget):
    """同じ教材用合成画像で、ROI・背景法・極性の影響を体験する。"""

    AMPS = [30, 60, 90, 120]

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        intro = QLabel("<b>教材用合成データ</b>（実験由来ではありません）。4本のバンドは背景から 30・60・90・120 だけ暗く、"
                       "真の量の比は 1 : 2 : 3 : 4 です。背景には上下方向の傾きとノイズがあります。"
                       "設定を変えて、測定値 S と「バンド1 に対する比」がどう変わるか確かめてください。")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#202020")
        vb = self.plot.getViewBox()
        vb.setAspectLocked(True)
        vb.invertY(True)
        vb.setMouseEnabled(False, False)
        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        split.addWidget(self.plot)
        right = QWidget()
        form = QFormLayout(right)
        self.sl_h = QSlider(Qt.Horizontal); self.sl_h.setRange(4, 40); self.sl_h.setValue(12)
        self.lbl_h = QLabel()
        form.addRow("ROI の高さ (px)", self.sl_h)
        form.addRow("", self.lbl_h)
        self.cmb_bg = QComboBox()
        for m in (BG_LOCAL, BG_MANUAL, BG_NONE):
            self.cmb_bg.addItem(BG_LABELS[m], m)
        form.addRow("背景の推定法", self.cmb_bg)
        self.cmb_manual = QComboBox()
        self.cmb_manual.addItem("画像の上端付近（背景が明るい）", "top")
        self.cmb_manual.addItem("バンドと同じ高さのレーン間", "middle")
        self.cmb_manual.addItem("画像の下端付近（背景が暗い）", "bottom")
        form.addRow("手動背景ROIの位置", self.cmb_manual)
        self.sl_gap = QSlider(Qt.Horizontal); self.sl_gap.setRange(0, 20); self.sl_gap.setValue(3)
        form.addRow("近傍背景の間隔 (px)", self.sl_gap)
        self.chk_grad = QCheckBox("背景に上下方向の傾きをつける")
        self.chk_grad.setChecked(True)
        form.addRow(self.chk_grad)
        self.cmb_pol = QComboBox()
        self.cmb_pol.addItem("暗いバンド（正しい設定）", "dark")
        self.cmb_pol.addItem("明るいバンド（誤った設定）", "bright")
        form.addRow("極性", self.cmb_pol)
        self.chk_inv = QCheckBox("表示を反転（表示のみ）")
        form.addRow(self.chk_inv)
        self.tbl = QTableWidget(4, 5)
        self.tbl.setHorizontalHeaderLabels(["バンド", "真の比", "S", "バンド1 に対する比", "b（背景）"])
        self.tbl.verticalHeader().setVisible(False)
        form.addRow(self.tbl)
        self.lbl_msg = QLabel()
        self.lbl_msg.setWordWrap(True)
        form.addRow(self.lbl_msg)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        for w in (self.sl_h, self.sl_gap):
            w.valueChanged.connect(self.update_view)
        for w in (self.cmb_bg, self.cmb_manual, self.cmb_pol):
            w.currentIndexChanged.connect(self.update_view)
        for w in (self.chk_grad, self.chk_inv):
            w.toggled.connect(self.update_view)
        self._rects: list[QGraphicsRectItem] = []
        self.update_view()

    def update_view(self) -> None:
        blot = make_blot(self.AMPS, background=190, gradient=0.35 if self.chk_grad.isChecked() else 0.0,
                         noise_sd=3, seed=3, height=140, band_height=10, blur_sigma=1.0)
        data = blot.data
        lut = np.arange(256, dtype=np.uint8)
        if self.chk_inv.isChecked():
            lut = lut[::-1].copy()
        self.img.setImage(data, levels=(0, 255), lut=lut, autoLevels=False)
        self.img.setRect(QRectF(0, 0, data.shape[1], data.shape[0]))
        for r in self._rects:
            self.plot.removeItem(r)
        self._rects.clear()

        h = self.sl_h.value()
        method = self.cmb_bg.currentData()
        pol = self.cmb_pol.currentData()
        gap = self.sl_gap.value()
        self.cmb_manual.setEnabled(method == BG_MANUAL)
        self.sl_gap.setEnabled(method == BG_LOCAL)
        mpos = self.cmb_manual.currentData()
        lane0 = blot.lanes[0]
        manual = {"top": Rect(2, 2, data.shape[1] - 4, 8),
                  "middle": Rect(lane0.x2 + 2, blot.bands[0].y, 8, blot.bands[0].h),
                  "bottom": Rect(2, data.shape[0] - 10, data.shape[1] - 4, 8)}[mpos]
        values = []
        for i, b in enumerate(blot.bands):
            cy = b.y + b.h / 2
            roi = Rect(b.x, round(cy - h / 2), b.w, h)
            bg = estimate_background(data, roi, method, pol, manual_rois=[manual], gap=gap, height=5)
            m = measure_band(data, roi, pol, bg.value)
            values.append((m.corrected, bg.value))
            self._add_rect(roi, QColor("#FF3030"))
            for r in bg.rects if method == BG_LOCAL else []:
                self._add_rect(r, QColor(255, 190, 0), fill=True)
        if method == BG_MANUAL:
            self._add_rect(manual, QColor("#20C020"))
        s1 = values[0][0]
        for i, (s, b) in enumerate(values):
            for c, v in enumerate((f"バンド{i + 1}", f"{i + 1}", fmt(s, 7), fmt(s / s1 if s1 else float('nan'), 3), fmt(b, 5))):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tbl.setItem(i, c, it)
        self.tbl.resizeColumnsToContents()
        self.lbl_h.setText(f"{h} px（バンドの実際の高さは 10 px、ぼかしで裾が少し広がっています）")
        msgs = []
        if pol == "bright":
            msgs.append("極性が逆のため S が負になっています。<b>符号の定義が逆になっただけで、画像は同じ</b>です。")
        if method == BG_NONE:
            msgs.append("背景補正なしでは、白（255）からの差をすべて数えるため、背景の分だけ値が大きくなり、"
                        "<b>バンド間の比が 1:2:3:4 より小さく（1 に近く）</b>なります。ROI を高くするほど悪化します。")
        if method == BG_MANUAL and mpos != "middle" and self.chk_grad.isChecked():
            msgs.append("背景に傾きがあると、バンドから離れた場所の背景ROIでは b がずれ、比が歪みます。")
        if method == BG_LOCAL and gap == 0 and h < 12:
            msgs.append("間隔 0 で ROI が小さいと、ぼけたバンドの裾が背景帯に入り、背景が過大評価されます。")
        if h < 8:
            msgs.append("ROI がバンドより小さいと、バンドの一部しか積分されません。")
        msgs.append("「表示を反転」を切り替えても、<b>表の数値は変わりません</b>（表示だけの変更）。")
        self.lbl_msg.setText("<br>".join(f"・{m}" for m in msgs))

    def _add_rect(self, r: Rect, color: QColor, fill: bool = False) -> None:
        it = QGraphicsRectItem(r.x, r.y, r.w, r.h)
        if fill:
            c = QColor(color); c.setAlpha(90)
            it.setBrush(QBrush(c))
            it.setPen(QPen(Qt.NoPen))
        else:
            pen = QPen(color); pen.setCosmetic(True); pen.setWidth(2)
            it.setPen(pen)
        self.plot.addItem(it)
        self._rects.append(it)


class LearnPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("学習・ヘルプ", "Western blot 定量の考え方を学ぶ教材と、操作マニュアルです。"
                                  "教材の例はすべて教材用合成データです。"))
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        w = QWidget()
        h = QHBoxLayout(w)
        self.lst = QListWidget()
        for _f, title in LESSONS:
            self.lst.addItem(title)
        self.lst.setMaximumWidth(260)
        self.lst.currentRowChanged.connect(self._show_lesson)
        h.addWidget(self.lst)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        h.addWidget(self.browser, 1)
        self.tabs.addTab(w, "教材（7ページ）")
        self.tabs.addTab(CompareWidget(), "比較・体験")

        w = QWidget()
        h = QHBoxLayout(w)
        self.lst_docs = QListWidget()
        for _f, title in DOCS:
            self.lst_docs.addItem(title)
        self.lst_docs.setMaximumWidth(260)
        self.lst_docs.currentRowChanged.connect(self._show_doc)
        h.addWidget(self.lst_docs)
        self.doc_browser = QTextBrowser()
        self.doc_browser.setOpenExternalLinks(True)
        self.doc_browser.setSearchPaths([str(DOCS_DIR)])   # マニュアル内の images/*.png を表示する
        h.addWidget(self.doc_browser, 1)
        self.tabs.addTab(w, "マニュアル・資料")
        self.lst.setCurrentRow(0)
        self.lst_docs.setCurrentRow(0)

    def open_lesson(self, number: int) -> None:
        self.tabs.setCurrentIndex(0)
        self.lst.setCurrentRow(number - 1)

    def open_manual(self) -> None:
        self.tabs.setCurrentIndex(2)
        self.lst_docs.setCurrentRow(0)

    def _show_lesson(self, row: int) -> None:
        if 0 <= row < len(LESSONS):
            self.browser.setMarkdown(read_text(LESSON_DIR / LESSONS[row][0]))
            self.browser.verticalScrollBar().setValue(0)

    def _show_doc(self, row: int) -> None:
        if 0 <= row < len(DOCS):
            self.doc_browser.setMarkdown(read_text(DOCS_DIR / DOCS[row][0]))
            self.doc_browser.verticalScrollBar().setValue(0)
