"""レーン・バンド ROI の編集画面（画像表示・プロファイル・リアルタイム測定）。"""
from __future__ import annotations

import dataclasses
import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGraphicsRectItem, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..core.imageio import dtype_max
from ..core.lanes import suggest_lanes
from ..core.pipeline import measure_one
from ..core.quant import lane_profile, local_background_rects, profile_peak_area
from ..models.project import (
    BG_LABELS, BG_LOCAL, BG_MANUAL, BG_METHODS, POLARITY_BRIGHT, POLARITY_DARK, BandROI, Lane, Rect, Sample,
)
from .widgets import HintBox, fmt, page_header

LANE_COLOR = QColor("#1E90FF")
BAND_COLOR = QColor("#FF3030")
BG_COLOR = QColor("#20C020")
LOCALBG_COLOR = QColor(255, 190, 0, 70)

DISPLAY_MODES = [
    ("raw", "原画像（全範囲・線形）"),
    ("invert", "反転表示（表示のみ）"),
    ("auto", "自動コントラスト（表示のみ）"),
    ("auto_invert", "反転＋自動コントラスト（表示のみ）"),
]
POLARITY_ITEMS = [(POLARITY_DARK, "暗いバンド（フィルムのスキャン像）"), (POLARITY_BRIGHT, "明るいバンド（反転済み・蛍光等）")]


def rect_of(roi: pg.ROI) -> Rect:
    p, s = roi.pos(), roi.size()
    return Rect(round(p.x()), round(p.y()), max(round(s.x()), 1), max(round(s.y()), 1))


def make_roi(rect: Rect, color: QColor, z: float, dashed: bool = False) -> pg.ROI:
    pen = pg.mkPen(color, width=1.5, style=Qt.DashLine if dashed else Qt.SolidLine)
    roi = pg.ROI([rect.x, rect.y], [rect.w, rect.h], pen=pen, snapSize=1, translateSnap=True, scaleSnap=True,
                 rotatable=False, removable=False)
    roi.hoverPen = pg.mkPen(color, width=2.5)
    roi.handlePen = pg.mkPen(color)
    for pos, center in (([1, 1], [0, 0]), ([0, 0], [1, 1]), ([1, 0.5], [0, 0.5]), ([0, 0.5], [1, 0.5]),
                        ([0.5, 1], [0.5, 0]), ([0.5, 0], [0.5, 1])):
        roi.addScaleHandle(pos, center)
    roi.setZValue(z)
    roi.setAcceptedMouseButtons(Qt.LeftButton)
    return roi


class RoiPage(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.image_id: str = ""
        self.selected: tuple[str, str] | None = None   # ("lane"|"band"|"bg", id)
        self._building = False
        self._items: dict[tuple[str, str], pg.ROI] = {}
        self._localbg_items: list[QGraphicsRectItem] = []
        self._fitted_for = ""
        self._build_ui()
        ctrl.changed.connect(self.refresh)
        ctrl.projectReplaced.connect(self._on_replaced)

    # ================================================================ UI 構築
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(page_header(
            "レーン・バンドROI",
            "画像上でレーンとバンドの矩形 ROI を設定します。ROI 座標は原画像の画素単位で保存され、拡大率に依存しません。"))
        self.hint = HintBox(
            "ROI を動かすと右下の値が<b>リアルタイムに</b>変わります。<b>表示の切替（反転・コントラスト）は測定値を変えません。</b>"
            "測定は常に原画像の画素値 p で行い、<b>極性</b>は S の符号 s（暗いバンド −1／明るいバンド +1）を決めます。"
            "同じタンパク質のバンドは<b>同じ大きさの ROI</b>で測るのが原則です。", lesson=2)
        root.addWidget(self.hint)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("画像："))
        self.cmb_image = QComboBox()
        self.cmb_image.setMinimumWidth(260)
        self.cmb_image.currentIndexChanged.connect(self._on_image_combo)
        bar.addWidget(self.cmb_image)
        bar.addWidget(QLabel("表示："))
        self.cmb_display = QComboBox()
        for key, label in DISPLAY_MODES:
            self.cmb_display.addItem(label, key)
        self.cmb_display.currentIndexChanged.connect(self._update_display)
        bar.addWidget(self.cmb_display)
        bar.addWidget(QLabel("バンド極性："))
        self.cmb_polarity = QComboBox()
        for key, label in POLARITY_ITEMS:
            self.cmb_polarity.addItem(label, key)
        self.cmb_polarity.setToolTip("フィルムをスキャンした画像では、バンドは背景より暗く（画素値が小さく）なります。")
        self.cmb_polarity.activated.connect(self._on_polarity)
        bar.addWidget(self.cmb_polarity)
        bar.addStretch(1)
        self.lbl_pixel = QLabel("")
        self.lbl_pixel.setStyleSheet("color:#444; font-family: Menlo, monospace;")
        bar.addWidget(self.lbl_pixel)
        root.addLayout(bar)
        self.lbl_display_note = QLabel("")
        self.lbl_display_note.setStyleSheet("color:#8a5a00;")
        root.addWidget(self.lbl_display_note)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ---- 左：画像
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#202020")
        self.vb = self.plot.getViewBox()
        self.vb.setAspectLocked(True)
        self.vb.invertY(True)
        self.vb.setMenuEnabled(False)
        self.img_item = pg.ImageItem()
        self.plot.addItem(self.img_item)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)
        split.addWidget(self.plot)

        # ---- 右
        right = QSplitter(Qt.Vertical)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.prof = pg.PlotWidget()
        self.prof.setBackground("w")
        self.prof.setLabel("bottom", "y（行、原画像の画素）")
        self.prof.setLabel("left", "行平均の画素値 p（原画像）")
        self.prof.setMinimumHeight(170)
        self.prof_curve = self.prof.plot(pen=pg.mkPen("#222", width=1.5))
        self.prof_region = pg.LinearRegionItem(orientation="vertical", brush=QBrush(QColor(255, 48, 48, 50)),
                                               pen=pg.mkPen(BAND_COLOR))
        self.prof_region.setZValue(10)
        self.prof_region.sigRegionChangeFinished.connect(self._on_prof_region_finished)
        self.prof.addItem(self.prof_region)
        self.prof_bgline = pg.InfiniteLine(angle=0, pen=pg.mkPen("#E08000", width=1.5, style=Qt.DashLine))
        self.prof.addItem(self.prof_bgline)
        self.prof_bgregions: list[pg.LinearRegionItem] = []
        self.prof_base = self.prof.plot(pen=pg.mkPen("#7030A0", width=1, style=Qt.DotLine))
        tl.addWidget(self.prof)
        self.lbl_prof = QLabel("レーンかバンドを選択すると、そのレーンの輝度プロファイルを表示します。")
        self.lbl_prof.setWordWrap(True)
        self.lbl_prof.setStyleSheet("color:#444;")
        tl.addWidget(self.lbl_prof)
        tl.addWidget(self._build_controls())
        right.addWidget(top)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tbl_bands = QTableWidget()
        self.tbl_lanes = QTableWidget()
        self.tabs.addTab(self.tbl_bands, "バンドと測定値")
        self.tabs.addTab(self.tbl_lanes, "レーンと sample_id")
        for t in (self.tbl_bands, self.tbl_lanes):
            t.setSelectionBehavior(QAbstractItemView.SelectRows)
            t.setSelectionMode(QAbstractItemView.SingleSelection)
            t.verticalHeader().setVisible(False)
            t.verticalHeader().setDefaultSectionSize(22)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_bands.itemSelectionChanged.connect(lambda: self._on_table_select(self.tbl_bands, "band"))
        self.tbl_lanes.itemSelectionChanged.connect(lambda: self._on_table_select(self.tbl_lanes, "lane"))
        self.tbl_bands.itemChanged.connect(self._on_band_item_changed)
        bl.addWidget(self.tabs, 1)
        self.lbl_formula = QLabel("")
        self.lbl_formula.setTextFormat(Qt.RichText)
        self.lbl_formula.setWordWrap(True)
        self.lbl_formula.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_formula.setStyleSheet("background:#F4F6FA; border:1px solid #C8D0E0; padding:6px;"
                                       "font-family: Menlo, monospace;")
        bl.addWidget(self.lbl_formula)
        right.addWidget(bottom)
        right.setSizes([480, 420])

        QShortcut(QKeySequence.Delete, self, activated=self._delete_selected)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, activated=self._delete_selected)

    def _build_controls(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)

        g1 = QGroupBox("① レーン")
        f1 = QGridLayout(g1)
        self.spn_nlanes = QSpinBox()
        self.spn_nlanes.setRange(1, 40)
        self.spn_nlanes.setValue(8)
        f1.addWidget(QLabel("レーン数"), 0, 0)
        f1.addWidget(self.spn_nlanes, 0, 1)
        b = QPushButton("レーン候補を提示")
        b.setToolTip("いま表示している範囲の列方向プロファイルからレーン中心の候補を探します。必ず目視で確認・修正してください。")
        b.clicked.connect(self._suggest_lanes)
        f1.addWidget(b, 1, 0, 1, 2)
        b = QPushButton("レーンを1本追加")
        b.clicked.connect(self._add_lane)
        f1.addWidget(b, 2, 0, 1, 2)
        grid.addWidget(g1, 0, 0)

        g2 = QGroupBox("② バンドROI")
        f2 = QGridLayout(g2)
        b = QPushButton("選択レーンにバンドを追加")
        b.clicked.connect(self._add_band)
        f2.addWidget(b, 0, 0, 1, 2)
        b = QPushButton("選択バンドを全レーンに複製")
        b.setToolTip("選択バンドと同じ y 位置・幅・高さで、バンドの無いレーンに ROI を作ります。")
        b.clicked.connect(self._replicate_band)
        f2.addWidget(b, 1, 0, 1, 2)
        b = QPushButton("ROIサイズを揃える")
        b.setToolTip("選択バンドと同じタンパク質のバンドをすべて、選択バンドと同じ幅・高さにします（中心は維持）。")
        b.clicked.connect(self._unify_size)
        f2.addWidget(b, 2, 0, 1, 2)
        self.spn_w = QSpinBox(); self.spn_w.setRange(1, 100000)
        self.spn_h = QSpinBox(); self.spn_h.setRange(1, 100000)
        f2.addWidget(QLabel("幅 / 高さ"), 3, 0)
        hb = QHBoxLayout(); hb.addWidget(self.spn_w); hb.addWidget(self.spn_h)
        f2.addLayout(hb, 3, 1)
        b = QPushButton("選択バンドだけ個別に変更")
        b.clicked.connect(self._apply_individual_size)
        f2.addWidget(b, 4, 0, 1, 2)
        grid.addWidget(g2, 0, 1)

        g3 = QGroupBox("③ 背景（この画像の既定）")
        f3 = QFormLayout(g3)
        self.cmb_bg = QComboBox()
        for m in BG_METHODS:
            self.cmb_bg.addItem(BG_LABELS[m], m)
        self.cmb_bg.activated.connect(self._on_bg_method)
        f3.addRow("方法", self.cmb_bg)
        self.spn_gap = QSpinBox(); self.spn_gap.setRange(0, 1000)
        self.spn_bgh = QSpinBox(); self.spn_bgh.setRange(1, 1000)
        self.spn_gap.editingFinished.connect(self._on_bg_params)
        self.spn_bgh.editingFinished.connect(self._on_bg_params)
        hb = QHBoxLayout(); hb.addWidget(QLabel("間隔")); hb.addWidget(self.spn_gap); hb.addWidget(QLabel("高さ")); hb.addWidget(self.spn_bgh)
        f3.addRow("近傍(px)", hb)
        b = QPushButton("手動背景ROIを追加")
        b.clicked.connect(self._add_bg_roi)
        f3.addRow(b)
        grid.addWidget(g3, 0, 2)

        hb = QHBoxLayout()
        b = QPushButton("選択ROIを削除（Delete）")
        b.clicked.connect(self._delete_selected)
        hb.addWidget(b)
        b = QPushButton("全体表示")
        b.clicked.connect(lambda: self.vb.autoRange(padding=0.02))
        hb.addWidget(b)
        hb.addStretch(1)
        grid.addLayout(hb, 1, 0, 1, 3)
        return w

    # ================================================================ 状態
    def current_image(self):
        return self.ctrl.project.image(self.image_id)

    def show_image(self, image_id: str) -> None:
        idx = self.cmb_image.findData(image_id)
        if idx >= 0:
            self.cmb_image.setCurrentIndex(idx)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def _on_replaced(self) -> None:
        self.selected = None
        self._fitted_for = ""

    def refresh(self) -> None:
        proj = self.ctrl.project
        self.cmb_image.blockSignals(True)
        self.cmb_image.clear()
        for im in proj.images:
            self.cmb_image.addItem(f"{im.image_id}  {im.protein_target or '(タンパク質未設定)'}  {im.exposure_id}", im.image_id)
        if not proj.image(self.image_id):
            self.image_id = proj.images[0].image_id if proj.images else ""
        idx = self.cmb_image.findData(self.image_id)
        self.cmb_image.setCurrentIndex(idx)
        self.cmb_image.blockSignals(False)
        self._load_current()

    def _on_image_combo(self, _i: int) -> None:
        iid = self.cmb_image.currentData()
        if iid and iid != self.image_id:
            self.image_id = iid
            self.selected = None
            self._load_current()

    def _load_current(self) -> None:
        im = self.current_image()
        self._clear_rois()
        if im is None:
            self.img_item.clear()
            self.lbl_formula.setText("画像がありません。［画像・試料情報］で画像を追加してください。")
            self._fill_tables()
            return
        data = self.ctrl.image_data(im.image_id)
        if data is None:
            self.img_item.clear()
            self.lbl_formula.setText(f"{im.image_id} を読み込めません。［画像・試料情報］で再リンクしてください。")
            self._fill_tables()
            return
        self.cmb_polarity.setCurrentIndex(0 if im.polarity == POLARITY_DARK else 1)
        self.cmb_bg.setCurrentIndex(BG_METHODS.index(im.bg_method))
        self.spn_gap.setValue(im.bg_gap)
        self.spn_bgh.setValue(im.bg_height)
        self._update_display()
        if self._fitted_for != im.image_id:
            self.vb.autoRange(padding=0.02)
            self._fitted_for = im.image_id
        self._build_rois()
        self._fill_tables()
        self._update_selection_views()

    def _update_display(self) -> None:
        im = self.current_image()
        data = self.ctrl.image_data(im.image_id) if im else None
        if data is None:
            return
        mode = self.cmb_display.currentData()
        vmax = dtype_max(data.dtype)
        if mode in ("auto", "auto_invert"):
            lo, hi = np.percentile(data, [0.5, 99.5])
            if hi <= lo:
                lo, hi = 0, vmax
        else:
            lo, hi = 0, vmax
        lut = np.arange(256, dtype=np.uint8)
        if mode in ("invert", "auto_invert"):
            lut = lut[::-1].copy()
        # 原画像配列をそのまま渡し、表示上の変換は levels と LUT だけで行う（配列は変更しない）
        self.img_item.setImage(data, levels=(float(lo), float(hi)), lut=lut, autoLevels=False)
        self.img_item.setRect(QRectF(0, 0, data.shape[1], data.shape[0]))
        notes = {
            "raw": f"表示：原画像の値をそのまま 0–{vmax} で線形表示。",
            "invert": "表示：反転（表示のみ）。測定値は変わりません。",
            "auto": f"表示：自動コントラスト {lo:.0f}–{hi:.0f}（表示のみ）。測定値は変わりません。",
            "auto_invert": f"表示：反転＋自動コントラスト {lo:.0f}–{hi:.0f}（表示のみ）。測定値は変わりません。",
        }
        self.lbl_display_note.setText(
            f"{notes[mode]}　画像：{data.shape[1]}×{data.shape[0]} px、{im.bit_depth}-bit（{im.dtype}）、範囲 0–{vmax}")

    # ================================================================ ROI 描画
    def _clear_rois(self) -> None:
        for roi in self._items.values():
            self.plot.removeItem(roi)
        self._items.clear()
        for it in self._localbg_items:
            self.plot.removeItem(it)
        self._localbg_items.clear()

    def _build_rois(self) -> None:
        im = self.current_image()
        self._building = True
        try:
            for l in im.lanes:
                self._add_roi_item(("lane", l.lane_id), l.rect, LANE_COLOR, 5, dashed=True)
            for b in im.bands:
                self._add_roi_item(("band", b.band_id), b.rect, BAND_COLOR, 10)
            for i, r in enumerate(im.bg_rois):
                self._add_roi_item(("bg", str(i)), r, BG_COLOR, 8)
        finally:
            self._building = False
        self._draw_local_bg()
        self._apply_selection_pens()

    def _add_roi_item(self, key, rect: Rect, color: QColor, z: float, dashed: bool = False) -> None:
        roi = make_roi(rect, color, z, dashed)
        roi.sigClicked.connect(lambda _r, _ev, k=key: self._select(k))
        roi.sigRegionChangeStarted.connect(lambda _r, k=key: self._select(k, from_drag=True))
        roi.sigRegionChanged.connect(lambda _r, k=key: self._on_roi_live(k))
        roi.sigRegionChangeFinished.connect(lambda _r, k=key: self._on_roi_finished(k))
        self.plot.addItem(roi)
        self._items[key] = roi

    def _draw_local_bg(self, override: dict | None = None) -> None:
        for it in self._localbg_items:
            self.plot.removeItem(it)
        self._localbg_items.clear()
        im = self.current_image()
        if im is None:
            return
        for b in im.bands:
            if im.band_bg_method(b) != BG_LOCAL:
                continue
            rect = (override or {}).get(b.band_id, b.rect)
            for r in local_background_rects(rect, im.bg_gap, im.bg_height, im.width, im.height):
                it = QGraphicsRectItem(r.x, r.y, r.w, r.h)
                it.setBrush(QBrush(LOCALBG_COLOR))
                it.setPen(QPen(Qt.NoPen))
                it.setZValue(7)
                self.plot.addItem(it)
                self._localbg_items.append(it)

    def _apply_selection_pens(self) -> None:
        for key, roi in self._items.items():
            color = {"lane": LANE_COLOR, "band": BAND_COLOR, "bg": BG_COLOR}[key[0]]
            width = 3.5 if key == self.selected else 1.5
            roi.setPen(pg.mkPen(color, width=width, style=Qt.DashLine if key[0] == "lane" else Qt.SolidLine))

    # ================================================================ 選択
    def _select(self, key, from_drag: bool = False) -> None:
        if self._building:
            return
        if self.selected == key:
            return
        self.selected = key
        self._apply_selection_pens()
        self._sync_table_selection()
        self._update_selection_views()

    def _on_table_select(self, table: QTableWidget, kind: str) -> None:
        if self._building:
            return
        rows = table.selectionModel().selectedRows()
        if not rows:
            return
        item = table.item(rows[0].row(), 0)
        if item is None:
            return
        key = (kind, item.text())
        if key != self.selected:
            self.selected = key
            self._apply_selection_pens()
            self._update_selection_views()

    def _sync_table_selection(self) -> None:
        self._building = True
        try:
            if self.selected and self.selected[0] in ("band", "lane"):
                table = self.tbl_bands if self.selected[0] == "band" else self.tbl_lanes
                for r in range(table.rowCount()):
                    if table.item(r, 0) and table.item(r, 0).text() == self.selected[1]:
                        table.selectRow(r)
                        break
        finally:
            self._building = False

    # ================================================================ ROI 操作（ドラッグ）
    def _on_roi_live(self, key) -> None:
        """ドラッグ中：モデルは変えずに、測定値・プロファイルを仮の矩形で更新する。"""
        if self._building:
            return
        roi = self._items.get(key)
        if roi is None:
            return
        rect = rect_of(roi)
        if key[0] == "band":
            self._draw_local_bg({key[1]: rect})
            self._update_selection_views(temp_band=(key[1], rect))
        elif key[0] == "lane":
            self._update_selection_views(temp_lane=(key[1], rect))

    def _on_roi_finished(self, key) -> None:
        if self._building:
            return
        im = self.current_image()
        roi = self._items.get(key)
        if im is None or roi is None:
            return
        new = rect_of(roi)
        if key[0] == "band":
            b = im.band(key[1])
            if b is None or b.rect == new:
                return
            old = b.rect
            b.rect = new
            self.ctrl.commit("バンドROI変更", f"{im.image_id}/{b.band_id}: {old.as_tuple()} → {new.as_tuple()}")
        elif key[0] == "lane":
            l = im.lane(key[1])
            if l is None or l.rect == new:
                return
            old = l.rect
            l.rect = new
            dx = (new.x + new.w / 2) - (old.x + old.w / 2)
            moved = []
            if abs(dx) >= 1 and new.w == old.w:   # レーンを横に動かしたら所属バンドも一緒に動かす
                for b in im.bands:
                    if b.lane_id == l.lane_id:
                        b.rect = Rect(b.rect.x + round(dx), b.rect.y, b.rect.w, b.rect.h)
                        moved.append(b.band_id)
            extra = f"（バンド {', '.join(moved)} も x 方向に {round(dx):+d} px 移動）" if moved else ""
            self.ctrl.commit("レーン変更", f"{im.image_id}/{l.lane_id}: {old.as_tuple()} → {new.as_tuple()}{extra}")
        elif key[0] == "bg":
            i = int(key[1])
            if i >= len(im.bg_rois) or im.bg_rois[i] == new:
                return
            old = im.bg_rois[i]
            im.bg_rois[i] = new
            self.ctrl.commit("背景ROI変更", f"{im.image_id}/BG{i + 1}: {old.as_tuple()} → {new.as_tuple()}")

    def _on_prof_region_finished(self) -> None:
        """プロファイル上の帯をドラッグしたら、バンド ROI の y と高さに反映する。"""
        if self._building or not self.selected or self.selected[0] != "band":
            return
        im = self.current_image()
        b = im.band(self.selected[1]) if im else None
        if b is None:
            return
        y0, y1 = self.prof_region.getRegion()
        ny, nh = round(y0), max(round(y1) - round(y0), 1)
        if (ny, nh) == (b.rect.y, b.rect.h):
            return
        old = b.rect
        b.rect = Rect(b.rect.x, ny, b.rect.w, nh)
        self.ctrl.commit("バンドROI変更（プロファイル上）", f"{im.image_id}/{b.band_id}: {old.as_tuple()} → {b.rect.as_tuple()}")

    # ================================================================ 選択対象の表示（プロファイル・式）
    def _update_selection_views(self, temp_band=None, temp_lane=None) -> None:
        im = self.current_image()
        data = self.ctrl.image_data(im.image_id) if im else None
        if im is None or data is None or not self.selected:
            self.prof_curve.setData([], [])
            self.prof_base.setData([], [])
            self.prof_region.hide()
            self.prof_bgline.hide()
            self._clear_prof_bg()
            if im is not None and data is not None:
                self.lbl_formula.setText("ROI を選択すると、ここに計算式と数値が表示されます。")
            return
        kind, ident = self.selected
        band = lane = None
        if kind == "band":
            band = im.band(ident)
            if band is None:
                return
            if temp_band and temp_band[0] == ident:
                band = dataclasses.replace(band, rect=temp_band[1])
            lane = im.lane(band.lane_id)
        elif kind == "lane":
            lane = im.lane(ident)
        if lane is not None and temp_lane and temp_lane[0] == lane.lane_id:
            lane = dataclasses.replace(lane, rect=temp_lane[1])
        if kind == "bg":
            i = int(ident)
            r = rect_of(self._items[("bg", ident)]) if ("bg", ident) in self._items else im.bg_rois[i]
            px = data[max(r.y, 0):r.y2, max(r.x, 0):r.x2].astype(float)
            mean = px.mean() if px.size else float("nan")
            self.lbl_formula.setText(f"手動背景ROI BG{i + 1}：{r.as_tuple()}　画素数 {px.size}　平均 {fmt(mean)}<br>"
                                     "「手動背景ROIの平均」では、すべての手動背景ROIの全画素の平均を b とします。")
            return

        # ---- プロファイル
        prof_rect = lane.rect if lane is not None else (Rect(band.rect.x, 0, band.rect.w, im.height) if band else None)
        self._building = True
        try:
            if prof_rect is not None:
                ys, vals = lane_profile(data, prof_rect)
                self.prof_curve.setData(ys, vals)
                src = f"レーン {lane.lane_id}" if lane is not None else "バンドの x 範囲"
                self.lbl_prof.setText(f"{src} の各行について、幅 {prof_rect.w} px の平均画素値を表示しています（原画像の値）。"
                                      + ("暗いバンドは谷として見えます。" if im.polarity == POLARITY_DARK else "明るいバンドは山として見えます。"))
            self._clear_prof_bg()
            if band is not None:
                self.prof_region.setRegion((band.rect.y, band.rect.y2))
                self.prof_region.show()
                m = measure_one(self.ctrl.project, im, band, data)
                if math.isfinite(m["bg_value"]) and im.band_bg_method(band) != "none":
                    self.prof_bgline.setValue(m["bg_value"])
                    self.prof_bgline.show()
                else:
                    self.prof_bgline.hide()
                if im.band_bg_method(band) == BG_LOCAL:
                    for r in local_background_rects(band.rect, im.bg_gap, im.bg_height, im.width, im.height):
                        reg = pg.LinearRegionItem((r.y, r.y2), movable=False, brush=QBrush(LOCALBG_COLOR),
                                                  pen=pg.mkPen(None))
                        self.prof.addItem(reg)
                        self.prof_bgregions.append(reg)
                peak_txt = ""
                if prof_rect is not None:
                    area, yy, base = profile_peak_area(ys, vals, band.rect.y, band.rect.y2, im.polarity)
                    self.prof_base.setData(yy, base)
                    peak_txt = (f"<br><span style='color:#7030A0'>参考：プロファイル面積（バンド範囲の両端を結ぶ直線を基線）＝ {fmt(area)}"
                                "　※定義が異なるため S とは一致しません（理論・検証ドキュメント参照）。</span>")
                self._show_formula(im, band, m, peak_txt, temp=temp_band is not None)
                self.spn_w.setValue(band.rect.w)
                self.spn_h.setValue(band.rect.h)
            else:
                self.prof_region.hide()
                self.prof_bgline.hide()
                self.prof_base.setData([], [])
                self.lbl_formula.setText(f"レーン {lane.lane_id}：{lane.rect.as_tuple()}　sample_id：{lane.sample_id or '（未割当）'}")
        finally:
            self._building = False

    def _clear_prof_bg(self) -> None:
        for reg in self.prof_bgregions:
            self.prof.removeItem(reg)
        self.prof_bgregions.clear()

    def _show_formula(self, im, band: BandROI, m: dict, extra: str, temp: bool) -> None:
        s = m["sign"]
        method = m["bg_method"]
        if method == "none":
            bg_desc = (f"背景補正なし：b = {'白（dtype 最大値）' if im.polarity == POLARITY_DARK else '0'} = {fmt(m['bg_value'])}")
        elif method == BG_LOCAL:
            bg_desc = f"近傍背景：バンド上下の帯（間隔 {im.bg_gap}px・高さ {im.bg_height}px）{m['bg_n_pixels']} 画素の平均 b = {fmt(m['bg_value'])}"
        else:
            bg_desc = f"手動背景ROI {len(im.bg_rois)} 個、{m['bg_n_pixels']} 画素の平均 b = {fmt(m['bg_value'])}"
        warn = f"<br><span style='color:#B00000'>⚠ {m['warnings']}</span>" if m["warnings"] else ""
        status = "<span style='color:#B06000'>（ドラッグ中の仮の値）</span>" if temp else ""
        sample = m["sample_id"] or "（未割当）"
        self.lbl_formula.setText(
            f"<b>{band.band_id}</b>（{m['protein'] or 'タンパク質未設定'}／sample {sample}）ROI {band.rect.as_tuple()} {status}<br>"
            f"S = s × (Σp − N × b)<br>"
            f"&nbsp;&nbsp;= ({s:+d}) × ({fmt(m['raw_sum'], 10)} − {m['n_pixels']} × {fmt(m['bg_value'], 8)})<br>"
            f"&nbsp;&nbsp;= <b>{fmt(m['corrected'], 8)}</b>　（相対的な画像強度・較正なし）<br>"
            f"{bg_desc}{warn}{extra}")

    # ================================================================ テーブル
    def _fill_tables(self) -> None:
        im = self.current_image()
        self._building = True
        try:
            self._fill_lane_table(im)
            self._fill_band_table(im)
        finally:
            self._building = False
        self._sync_table_selection()

    def _fill_lane_table(self, im) -> None:
        t = self.tbl_lanes
        t.clear()
        cols = ["lane_id", "sample_id", "group", "bio_rep", "x", "y", "幅", "高さ"]
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        lanes = im.lanes if im else []
        t.setRowCount(len(lanes))
        sample_ids = [s.sample_id for s in self.ctrl.project.samples]
        for r, l in enumerate(lanes):
            it = QTableWidgetItem(l.lane_id)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, 0, it)
            cmb = QComboBox()
            cmb.setEditable(True)
            cmb.addItems([""] + sample_ids)
            cmb.setCurrentText(l.sample_id)
            cmb.setToolTip("一覧から選ぶか、新しい sample_id を入力して Enter（試料表に自動追加されます）。")
            cmb.lineEdit().editingFinished.connect(lambda c=cmb, lid=l.lane_id: self._on_lane_sample(lid, c.currentText()))
            cmb.activated.connect(lambda _i, c=cmb, lid=l.lane_id: self._on_lane_sample(lid, c.currentText()))
            t.setCellWidget(r, 1, cmb)
            s = self.ctrl.project.sample(l.sample_id)
            for c, v in enumerate([s.group if s else "", s.bio_rep if s else "", l.rect.x, l.rect.y, l.rect.w, l.rect.h], 2):
                it = QTableWidgetItem(str(v))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, c, it)

    BAND_COLS = ["band_id", "lane", "sample_id", "タンパク質", "背景法", "N（画素）", "Σp（補正前）", "b（背景）",
                 "S（補正後）", "除外", "除外理由", "警告"]

    def _fill_band_table(self, im) -> None:
        t = self.tbl_bands
        t.clear()
        t.setColumnCount(len(self.BAND_COLS))
        t.setHorizontalHeaderLabels(self.BAND_COLS)
        bands = im.bands if im else []
        t.setRowCount(len(bands))
        if not bands:
            return
        df = self.ctrl.measurements()
        df = df[df["image_id"] == im.image_id].set_index("band_id") if len(df) else df
        for r, b in enumerate(bands):
            m = df.loc[b.band_id] if b.band_id in getattr(df, "index", []) else None
            def ro(text):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                return it
            t.setItem(r, 0, ro(b.band_id))
            t.setItem(r, 1, ro(b.lane_id))
            t.setItem(r, 2, ro(m["sample_id"] if m is not None else ""))
            it = QTableWidgetItem(b.protein or im.protein_target)
            it.setToolTip("既定は画像のタンパク質名。1枚の画像に複数のタンパク質がある場合はここで変更します。")
            t.setItem(r, 3, it)
            cmb = QComboBox()
            cmb.addItem(f"画像の既定（{BG_LABELS[im.bg_method]}）", "")
            for meth in BG_METHODS:
                cmb.addItem(BG_LABELS[meth], meth)
            cmb.setCurrentIndex(max(cmb.findData(b.bg_method_override), 0))
            cmb.activated.connect(lambda _i, c=cmb, bid=b.band_id: self._on_band_bg(bid, c.currentData()))
            t.setCellWidget(r, 4, cmb)
            if m is not None:
                for c, key in ((5, "n_pixels"), (6, "raw_sum"), (7, "bg_value"), (8, "corrected")):
                    it = ro(fmt(m[key], 8) if key != "n_pixels" else str(m[key]))
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    t.setItem(r, c, it)
            chk = QTableWidgetItem()
            chk.setFlags((chk.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            chk.setCheckState(Qt.Checked if b.excluded else Qt.Unchecked)
            t.setItem(r, 9, chk)
            t.setItem(r, 10, QTableWidgetItem(b.exclude_reason))
            t.setItem(r, 11, ro(m["warnings"] if m is not None else ""))

    def _on_band_item_changed(self, item: QTableWidgetItem) -> None:
        if self._building:
            return
        im = self.current_image()
        bid = self.tbl_bands.item(item.row(), 0).text()
        b = im.band(bid)
        if b is None:
            return
        col = item.column()
        if col == 3:
            new = item.text().strip()
            val = "" if new == im.protein_target else new
            if val != b.protein:
                b.protein = val
                self.ctrl.commit("バンドのタンパク質変更", f"{im.image_id}/{bid}: {new}")
        elif col == 9:
            ex = item.checkState() == Qt.Checked
            if ex != b.excluded:
                b.excluded = ex
                self.ctrl.commit("バンド除外" if ex else "バンド除外を解除", f"{im.image_id}/{bid} 理由：{b.exclude_reason}")
        elif col == 10:
            if item.text() != b.exclude_reason:
                b.exclude_reason = item.text()
                self.ctrl.commit("除外理由を記録", f"{im.image_id}/{bid}: {item.text()}")

    def _on_band_bg(self, band_id: str, method: str) -> None:
        im = self.current_image()
        b = im.band(band_id)
        if b and b.bg_method_override != method:
            b.bg_method_override = method
            self.ctrl.commit("バンド個別の背景法", f"{im.image_id}/{band_id}: {BG_LABELS.get(method, '画像の既定')}")

    def _on_lane_sample(self, lane_id: str, sample_id: str) -> None:
        im = self.current_image()
        l = im.lane(lane_id) if im else None
        sample_id = sample_id.strip()
        if l is None or l.sample_id == sample_id:
            return
        old = l.sample_id
        l.sample_id = sample_id
        detail = f"{im.image_id}/{lane_id}: {old or '（なし）'} → {sample_id or '（なし）'}"
        if sample_id and self.ctrl.project.sample(sample_id) is None:
            self.ctrl.project.samples.append(Sample(sample_id))
            detail += "（試料表に新規追加。group・bio_rep を入力してください）"
        QTimer.singleShot(0, lambda: self.ctrl.commit("レーンの sample_id 割当", detail))

    # ================================================================ 画像単位の設定
    def _on_polarity(self, _i: int) -> None:
        im = self.current_image()
        new = self.cmb_polarity.currentData()
        if im and im.polarity != new:
            im.polarity = new
            self.ctrl.commit("バンド極性の変更", f"{im.image_id}: {new}（S の符号の定義が変わります）")

    def _on_bg_method(self, _i: int) -> None:
        im = self.current_image()
        new = self.cmb_bg.currentData()
        if im and im.bg_method != new:
            old = im.bg_method
            im.bg_method = new
            self.ctrl.commit("背景推定法の変更", f"{im.image_id}: {BG_LABELS[old]} → {BG_LABELS[new]}")
            if new == BG_MANUAL and not im.bg_rois:
                QMessageBox.information(self, "手動背景ROI", "手動背景ROIがまだありません。「手動背景ROIを追加」で、バンドの無い場所に置いてください。")

    def _on_bg_params(self) -> None:
        im = self.current_image()
        if im and (im.bg_gap, im.bg_height) != (self.spn_gap.value(), self.spn_bgh.value()):
            old = (im.bg_gap, im.bg_height)
            im.bg_gap, im.bg_height = self.spn_gap.value(), self.spn_bgh.value()
            self.ctrl.commit("近傍背景パラメータの変更", f"{im.image_id}: (間隔, 高さ) {old} → {(im.bg_gap, im.bg_height)}")

    # ================================================================ 追加・削除
    def _view_rect(self, im) -> Rect:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        return Rect(int(max(x0, 0)), int(max(y0, 0)), int(min(x1, im.width) - max(x0, 0)),
                    int(min(y1, im.height) - max(y0, 0)))

    def _suggest_lanes(self) -> None:
        im = self.current_image()
        data = self.ctrl.image_data(im.image_id) if im else None
        if data is None:
            return
        if im.lanes:
            n_b = len(im.bands)
            if QMessageBox.question(self, "レーン候補", f"既存のレーン {len(im.lanes)} 本とバンド {n_b} 個を削除して、"
                                    "新しい候補に置き換えますか？（元に戻す で復元できます）") != QMessageBox.Yes:
                return
        vr = self._view_rect(im)
        try:
            sug = suggest_lanes(data, self.spn_nlanes.value(), im.polarity, vr.y, vr.y2, vr.x, vr.x2)
        except ValueError as e:
            QMessageBox.warning(self, "レーン候補", f"{e}\n表示範囲を広げてからやり直してください。")
            return
        old_samples = [l.sample_id for l in im.lanes]
        im.lanes.clear()
        im.bands.clear()
        for i, r in enumerate(sug.rects):
            im.lanes.append(Lane(im.next_lane_id(), r, old_samples[i] if i < len(old_samples) else ""))
        self.ctrl.commit("レーン候補を提示", f"{im.image_id}: {sug.method}、{len(sug.rects)} 本、範囲 {vr.as_tuple()}")
        QMessageBox.information(self, "レーン候補", sug.message + "\n\n次に、レーン表で各レーンに sample_id を割り当ててください。")

    def _add_lane(self) -> None:
        im = self.current_image()
        if im is None:
            return
        vr = self._view_rect(im)
        w = max(min(vr.w // 10, 60), 4)
        r = Rect(vr.x + vr.w // 2 - w // 2, vr.y, w, max(vr.h, 1))
        lid = im.next_lane_id()
        im.lanes.append(Lane(lid, r))
        self.selected = ("lane", lid)
        self.ctrl.commit("レーン追加", f"{im.image_id}/{lid}: {r.as_tuple()}")

    def _add_band(self) -> None:
        im = self.current_image()
        if im is None:
            return
        lane = None
        if self.selected and self.selected[0] == "lane":
            lane = im.lane(self.selected[1])
        elif self.selected and self.selected[0] == "band":
            b = im.band(self.selected[1])
            lane = im.lane(b.lane_id) if b else None
        if lane is None:
            QMessageBox.information(self, "バンド追加", "先にレーンをクリックして選択してください。\n"
                                    "（バンドは必ずどれかのレーン＝sample_id に属します）")
            return
        vr = self._view_rect(im)
        h = max(min(lane.rect.h // 10, 20), 3)
        cy = max(min(vr.y + vr.h // 2, lane.rect.y2 - h), lane.rect.y)
        r = Rect(lane.rect.x, cy, lane.rect.w, h)
        bid = im.next_band_id()
        im.bands.append(BandROI(bid, lane.lane_id, r))
        self.selected = ("band", bid)
        self.ctrl.commit("バンドROI追加", f"{im.image_id}/{bid}（{lane.lane_id}）: {r.as_tuple()}")

    def _template_band(self, what: str):
        im = self.current_image()
        if not self.selected or self.selected[0] != "band":
            QMessageBox.information(self, what, "基準にするバンド ROI を1つクリックして選択してから実行してください。")
            return None, None
        return im, im.band(self.selected[1])

    def _replicate_band(self) -> None:
        im, tb = self._template_band("全レーンに複製")
        if tb is None:
            return
        prot = im.band_protein(tb)
        tl = im.lane(tb.lane_id)
        offset = (tb.rect.x + tb.rect.w / 2) - (tl.rect.x + tl.rect.w / 2) if tl else 0
        made = []
        for l in im.lanes:
            if any(b.lane_id == l.lane_id and im.band_protein(b) == prot for b in im.bands):
                continue
            cx = l.rect.x + l.rect.w / 2 + offset
            r = Rect(round(cx - tb.rect.w / 2), tb.rect.y, tb.rect.w, tb.rect.h)
            bid = im.next_band_id()
            im.bands.append(BandROI(bid, l.lane_id, r, protein=tb.protein, bg_method_override=tb.bg_method_override))
            made.append(f"{bid}({l.lane_id})")
        if not made:
            QMessageBox.information(self, "全レーンに複製", f"すべてのレーンに {prot} のバンドが既にあります。")
            return
        self.ctrl.commit("バンドROIを全レーンに複製", f"{im.image_id}: 基準 {tb.band_id} {tb.rect.as_tuple()} → {', '.join(made)}")

    def _unify_size(self) -> None:
        im, tb = self._template_band("ROIサイズを揃える")
        if tb is None:
            return
        prot = im.band_protein(tb)
        changed = []
        for b in im.bands:
            if b is tb or im.band_protein(b) != prot or (b.rect.w, b.rect.h) == (tb.rect.w, tb.rect.h):
                continue
            cx, cy = b.rect.x + b.rect.w / 2, b.rect.y + b.rect.h / 2
            old = b.rect
            b.rect = Rect(round(cx - tb.rect.w / 2), round(cy - tb.rect.h / 2), tb.rect.w, tb.rect.h)
            changed.append(f"{b.band_id} {old.as_tuple()}→{b.rect.as_tuple()}")
        if not changed:
            QMessageBox.information(self, "ROIサイズを揃える", "すでに同じ大きさです。")
            return
        self.ctrl.commit("ROIサイズを揃える", f"{im.image_id} {prot}: 基準 {tb.band_id} ({tb.rect.w}×{tb.rect.h}); " + "; ".join(changed))

    def _apply_individual_size(self) -> None:
        im, b = self._template_band("個別に変更")
        if b is None:
            return
        w, h = self.spn_w.value(), self.spn_h.value()
        if (w, h) == (b.rect.w, b.rect.h):
            return
        old = b.rect
        cx, cy = b.rect.x + b.rect.w / 2, b.rect.y + b.rect.h / 2
        b.rect = Rect(round(cx - w / 2), round(cy - h / 2), w, h)
        self.ctrl.commit("バンドROIサイズの個別修正", f"{im.image_id}/{b.band_id}: {old.as_tuple()} → {b.rect.as_tuple()}")

    def _add_bg_roi(self) -> None:
        im = self.current_image()
        if im is None:
            return
        vr = self._view_rect(im)
        s = max(min(vr.w, vr.h) // 10, 4)
        r = Rect(vr.x + s, vr.y + s, s, s)
        im.bg_rois.append(r)
        self.selected = ("bg", str(len(im.bg_rois) - 1))
        self.ctrl.commit("手動背景ROI追加", f"{im.image_id}/BG{len(im.bg_rois)}: {r.as_tuple()}")

    def _delete_selected(self) -> None:
        im = self.current_image()
        if im is None or not self.selected:
            return
        kind, ident = self.selected
        if kind == "band":
            b = im.band(ident)
            if b:
                im.bands.remove(b)
                self.selected = None
                self.ctrl.commit("バンドROI削除", f"{im.image_id}/{ident}: {b.rect.as_tuple()}")
        elif kind == "lane":
            l = im.lane(ident)
            if l is None:
                return
            bands = [b for b in im.bands if b.lane_id == ident]
            if bands and QMessageBox.question(self, "レーン削除", f"レーン {ident} に属するバンド {len(bands)} 個も削除します。よろしいですか？") != QMessageBox.Yes:
                return
            im.lanes.remove(l)
            for b in bands:
                im.bands.remove(b)
            self.selected = None
            self.ctrl.commit("レーン削除", f"{im.image_id}/{ident}（バンド {len(bands)} 個も削除）")
        elif kind == "bg":
            i = int(ident)
            if i < len(im.bg_rois):
                r = im.bg_rois.pop(i)
                self.selected = None
                self.ctrl.commit("手動背景ROI削除", f"{im.image_id}/BG{i + 1}: {r.as_tuple()}")

    # ================================================================ マウス位置の画素値
    def _on_mouse(self, pos) -> None:
        im = self.current_image()
        data = self.ctrl.image_data(im.image_id) if im else None
        if data is None:
            return
        pt = self.vb.mapSceneToView(pos)
        x, y = int(math.floor(pt.x())), int(math.floor(pt.y()))
        if 0 <= x < data.shape[1] and 0 <= y < data.shape[0]:
            self.lbl_pixel.setText(f"x={x} y={y}  p={int(data[y, x])}（原画像値）")
        else:
            self.lbl_pixel.setText("")
