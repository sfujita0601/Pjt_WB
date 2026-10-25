"""［背景補正・定量］［QC］画面。"""
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.qc import STUDENT_CHECKLIST
from ..models.project import BG_LABELS, BG_METHODS, POLARITY_BRIGHT, POLARITY_DARK, QC_DECISIONS, QCDecision, now_iso
from .widgets import DataFrameModel, HintBox, LazyRefreshMixin, level_color, make_table_view, page_header

MEAS_HEADERS = {
    "image_id": "画像", "band_id": "バンド", "lane_id": "レーン", "sample_id": "試料ID", "group": "群",
    "bio_rep": "生物学的反復", "tech_rep": "技術的反復", "protein": "タンパク質", "membrane_id": "膜", "exposure_id": "露光",
    "bit_depth": "bit",
    "image_included": "定量に使う", "excluded": "除外", "polarity": "極性", "sign": "s", "n_pixels": "N",
    "raw_sum": "Σp（補正前）", "raw_mean": "平均 p", "bg_method_label": "背景法", "bg_value": "b（背景）",
    "bg_sum": "N×b", "corrected": "S（補正後）", "n_low_extreme": "値0の画素", "n_high_extreme": "最大値の画素",
    "warnings": "警告",
}
MEAS_SHOW = ["image_id", "band_id", "lane_id", "sample_id", "group", "bio_rep", "protein", "membrane_id", "exposure_id", "bit_depth",
             "image_included", "excluded", "polarity", "sign", "n_pixels", "raw_sum", "bg_method_label", "bg_value",
             "bg_sum", "corrected", "n_low_extreme", "n_high_extreme", "warnings"]


# ===================================================================== 背景補正・定量
class QuantPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("背景補正・定量（background subtraction / quantification）",
                                  "全画像のバンドの測定値を一覧します。値は測定領域（ROI）・背景・極性の設定から毎回再計算されます。"))
        self.hint = HintBox(
            "背景補正の方法で S は大きく変わります。<b>近傍背景</b>はバンドの真上・真下の帯（バンドと同じ幅）の平均、"
            "<b>手動背景ROI</b>は自分で置いた背景の測定領域の平均、<b>補正なし</b>は暗いバンドなら白（最大値）を基準にします。"
            "S が負でも 0 に丸めません。値は較正されていない<b>相対的な画像強度</b>で、タンパク質量そのものではありません。", lesson=3)
        lay.addWidget(self.hint)
        formula = QLabel(
            "<b>計算式</b>：S = Σ<sub>i</sub> s·(p<sub>i</sub> − b) = s·(Σp − N·b)　"
            "p：原画像の画素値、b：背景推定値（ROI 内で一定）、N：ROI の画素数、s：暗いバンド −1／明るいバンド +1")
        formula.setStyleSheet("background:#F4F6FA; border:1px solid #C8D0E0; padding:6px;")
        lay.addWidget(formula)

        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)
        g = QGroupBox("画像ごとの測定設定（［レーン・バンドROI］画面と同じ設定です）")
        v = QVBoxLayout(g)
        note = QLabel("近傍背景の間隔・高さ（px）は画像の解像度とバンドの大きさに依存します。固定の標準値ではありません。"
                      "目安として、右の列にバンド ROI の高さ h に対する比率を表示しています（考え方はマニュアル 5 章）。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#555;")
        v.addWidget(note)
        self.tbl_img = QTableWidget()
        cols = ["image_id", "タンパク質", "bit", "定量に使う", "極性", "背景法（既定）", "近傍：間隔 px", "近傍：高さ px",
                "ROI 高さ h（中央値）に対する比", "手動背景ROI数", "バンド数"]
        self.tbl_img.setColumnCount(len(cols))
        self.tbl_img.setHorizontalHeaderLabels(cols)
        self.tbl_img.verticalHeader().setVisible(False)
        self.tbl_img.itemChanged.connect(self._on_item)
        v.addWidget(self.tbl_img)
        split.addWidget(g)

        g = QGroupBox("測定値（バンドごと）")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        b = QPushButton("測定値を CSV に保存…")
        b.clicked.connect(self._export)
        row.addWidget(b)
        b = QPushButton("Fiji/ImageJ と照合…")
        b.setToolTip("同じ ROI を Fiji で測るマクロを出力し、Fiji の結果と本アプリの値を比較します。")
        b.clicked.connect(self._validate)
        row.addWidget(b)
        self.lbl_count = QLabel()
        row.addWidget(self.lbl_count)
        row.addStretch(1)
        v.addLayout(row)
        self.model = DataFrameModel(headers=MEAS_HEADERS)
        self.view = make_table_view(self.model)
        v.addWidget(self.view)
        split.addWidget(g)
        split.setStretchFactor(1, 3)
        ctrl.changed.connect(self.request_refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def refresh(self) -> None:
        p = self.ctrl.project
        self._building = True
        try:
            t = self.tbl_img
            t.setRowCount(len(p.images))
            for r, im in enumerate(p.images):
                hmed = float(pd.Series([b.rect.h for b in im.bands]).median()) if im.bands else 0
                ratio = f"間隔 {im.bg_gap / hmed:.0%}・高さ {im.bg_height / hmed:.0%}（h = {hmed:g} px）" if hmed else "—"
                for c, v in ((0, im.image_id), (1, im.protein_target), (2, f"{im.bit_depth}"), (8, ratio),
                             (9, str(len(im.bg_rois))), (10, str(len(im.bands)))):
                    it = QTableWidgetItem(v)
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    t.setItem(r, c, it)
                chk = QTableWidgetItem()
                chk.setFlags((chk.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
                chk.setCheckState(Qt.Checked if im.include else Qt.Unchecked)
                t.setItem(r, 3, chk)
                cmb = QComboBox()
                cmb.addItem("暗いバンド", POLARITY_DARK)
                cmb.addItem("明るいバンド", POLARITY_BRIGHT)
                cmb.setCurrentIndex(0 if im.polarity == POLARITY_DARK else 1)
                cmb.activated.connect(lambda _i, c=cmb, iid=im.image_id: self._set(iid, "polarity", c.currentData(), "バンド極性の変更"))
                t.setCellWidget(r, 4, cmb)
                cmb = QComboBox()
                for m in BG_METHODS:
                    cmb.addItem(BG_LABELS[m], m)
                cmb.setCurrentIndex(BG_METHODS.index(im.bg_method))
                cmb.activated.connect(lambda _i, c=cmb, iid=im.image_id: self._set(iid, "bg_method", c.currentData(), "背景推定法の変更"))
                t.setCellWidget(r, 5, cmb)
                for c, attr in ((6, "bg_gap"), (7, "bg_height")):
                    sp = QSpinBox()
                    sp.setRange(0 if attr == "bg_gap" else 1, 1000)
                    sp.setValue(getattr(im, attr))
                    sp.editingFinished.connect(lambda s=sp, iid=im.image_id, a=attr: self._set(iid, a, s.value(), "近傍背景パラメータの変更"))
                    t.setCellWidget(r, c, sp)
            t.resizeColumnsToContents()
        finally:
            self._building = False
        df = self.ctrl.measurements()
        self.model.set_frame(df[MEAS_SHOW] if len(df) else pd.DataFrame(columns=MEAS_SHOW))
        self.view.resizeColumnsToContents()
        errs = self.ctrl.load_errors()
        self.lbl_count.setText(f"{len(df)} バンド" + (f"　⚠ 読み込めない画像：{', '.join(errs)}" if errs else ""))

    def _set(self, iid: str, attr: str, value, action: str) -> None:
        im = self.ctrl.project.image(iid)
        if im is None or getattr(im, attr) == value:
            return
        old = getattr(im, attr)
        setattr(im, attr, value)
        self.ctrl.commit(action, f"{iid}.{attr}: {old} → {value}")

    def _on_item(self, item) -> None:
        if self._building or item.column() != 3:
            return
        iid = self.tbl_img.item(item.row(), 0).text()
        QTimer.singleShot(0, lambda: self._set(iid, "include", item.checkState() == Qt.Checked, "定量に使う画像の変更"))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "測定値 CSV", "measurements.csv", "CSV (*.csv)")
        if path:
            self.ctrl.measurements().to_csv(path, index=False, encoding="utf-8-sig")

    def _validate(self) -> None:
        if not any(im.bands for im in self.ctrl.project.images):
            QMessageBox.information(self, "Fiji と照合", "バンド ROI のある画像がありません。")
            return
        from .validation_dialog import ValidationDialog
        ValidationDialog(self.ctrl, self).exec()


# ===================================================================== QC
class QCPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        self._issues = []
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("QC（品質管理、quality control）", "自動チェックで見つかった「疑い」の一覧です。確定診断ではありません。"
                                  "各項目について判断（採用／除外／再撮影推奨）と理由を記録してください。問題データは自動では削除しません。"))
        self.hint = HintBox(
            "画素値が 0 や最大値に張り付いていれば飽和の<b>疑い</b>がありますが、張り付いていなくても、フィルムやスキャナーの"
            "非線形性で信号が頭打ちになっていることがあります。<b>画像の画素値だけでは、信号とタンパク質量の比例関係は証明できません。</b>"
            "線形範囲の確認には希釈系列・露光時間の比較が必要です（本アプリでは Phase 2）。", lesson=6)
        lay.addWidget(self.hint)
        self.lbl_summary = QLabel()
        lay.addWidget(self.lbl_summary)
        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)
        self.tbl = QTableWidget()
        cols = ["レベル", "分類", "内容", "どう直すか", "判断", "理由", "key"]
        self.tbl.setColumnCount(len(cols))
        self.tbl.setHorizontalHeaderLabels(cols)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setWordWrap(True)
        self.tbl.itemChanged.connect(self._on_item)
        split.addWidget(self.tbl)
        g = QGroupBox("学生向けチェックリスト（画像からは自動判定できない項目）")
        v = QVBoxLayout(g)
        self.checks: list[QCheckBox] = []
        for i, text in enumerate(STUDENT_CHECKLIST):
            c = QCheckBox(text)
            c.toggled.connect(lambda on, k=str(i), t=text: self._on_check(k, t, on))
            v.addWidget(c)
            self.checks.append(c)
        split.addWidget(g)
        ctrl.changed.connect(self.request_refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def refresh(self) -> None:
        self._issues = self.ctrl.qc_issues()
        dec = self.ctrl.project.qc_decisions
        self._building = True
        try:
            t = self.tbl
            t.setRowCount(len(self._issues))
            for r, q in enumerate(self._issues):
                d = dec.get(q.key, QCDecision())
                for c, v in enumerate((q.level, q.category, q.message, q.how_to_fix)):
                    it = QTableWidgetItem(v)
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    col = level_color(q.level)
                    if col:
                        it.setBackground(col)
                    t.setItem(r, c, it)
                cmb = QComboBox()
                cmb.addItems(QC_DECISIONS)
                cmb.setCurrentText(d.decision)
                cmb.activated.connect(lambda _i, c=cmb, key=q.key: self._on_decision(key, c.currentText()))
                t.setCellWidget(r, 4, cmb)
                t.setItem(r, 5, QTableWidgetItem(d.reason))
                it = QTableWidgetItem(q.key)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, 6, it)
            t.setColumnWidth(2, 380)
            t.setColumnWidth(3, 380)
            t.resizeRowsToContents()
            for i, c in enumerate(self.checks):
                c.blockSignals(True)
                c.setChecked(self.ctrl.project.settings.checklist.get(str(i), False))
                c.blockSignals(False)
        finally:
            self._building = False
        n_pending = sum(1 for q in self._issues if dec.get(q.key, QCDecision()).decision == "未確認")
        self.lbl_summary.setText(f"検出 {len(self._issues)} 件（未確認 {n_pending} 件）。"
                                 "判断を「除外」にすると、その項目に関係するバンドが正規化・統計から外れます（データは残ります）。")

    def _issue(self, key):
        return next((q for q in self._issues if q.key == key), None)

    def _on_decision(self, key: str, decision: str) -> None:
        p = self.ctrl.project
        d = p.qc_decisions.get(key, QCDecision())
        if d.decision == decision:
            return
        q = self._issue(key)
        old = d.decision
        d.decision, d.decided_at = decision, now_iso()
        p.qc_decisions[key] = d
        affected = []
        if q and q.image_id and q.band_ids:
            im = p.image(q.image_id)
            for bid in q.band_ids:
                b = im.band(bid) if im else None
                if b is None:
                    continue
                if decision == "除外":
                    b.excluded, b.exclude_reason = True, d.reason or f"QC：{q.category}"
                    affected.append(bid)
                elif old == "除外":
                    b.excluded = False
                    affected.append(bid)
        if decision == "除外" and not (q and q.band_ids):
            QMessageBox.information(self, "QC 判断", "この項目は特定のバンドに結びついていないため、判断の記録のみ行いました。"
                                    "データを外す場合は［画像・試料情報］の「定量に使う」や、バンド表の「除外」を使ってください。")
        extra = f"（バンド {', '.join(affected)} の除外状態を変更）" if affected else ""
        self.ctrl.commit("QC 判断", f"{key}: {old} → {decision}{extra}")

    def _on_item(self, item) -> None:
        if self._building or item.column() != 5:
            return
        key = self.tbl.item(item.row(), 6).text()
        p = self.ctrl.project
        d = p.qc_decisions.get(key, QCDecision())
        if d.reason == item.text():
            return
        d.reason, d.decided_at = item.text(), now_iso()
        p.qc_decisions[key] = d
        q = self._issue(key)
        if d.decision == "除外" and q and q.image_id:
            im = p.image(q.image_id)
            for bid in q.band_ids:
                b = im.band(bid) if im else None
                if b:
                    b.exclude_reason = d.reason
        QTimer.singleShot(0, lambda: self.ctrl.commit("QC 判断の理由", f"{key}: {d.reason}"))

    def _on_check(self, key: str, text: str, on: bool) -> None:
        self.ctrl.project.settings.checklist[key] = on
        self.ctrl.commit("チェックリスト", f"{'確認' if on else '未確認'}：{text[:40]}…")
