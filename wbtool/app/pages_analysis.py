"""［背景補正・定量］［正規化］［QC］［統計］［Figure］画面。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core.figure import PLOT_KINDS, FigureStyle, japanese_font_candidates, plot_groups, save_figure
from ..core.normalize import STEP_DESCRIPTION
from ..core.qc import STUDENT_CHECKLIST
from ..core.stats import describe, two_group_test
from ..models.project import BG_LABELS, BG_METHODS, POLARITY_BRIGHT, POLARITY_DARK, QC_DECISIONS, QCDecision, now_iso
from .widgets import DataFrameModel, HintBox, LazyRefreshMixin, fmt, level_color, make_table_view, page_header

MEAS_HEADERS = {
    "image_id": "画像", "band_id": "バンド", "lane_id": "レーン", "sample_id": "sample_id", "group": "群",
    "bio_rep": "bio_rep", "tech_rep": "tech_rep", "protein": "タンパク質", "membrane_id": "膜", "exposure_id": "露光",
    "image_included": "定量に使う", "excluded": "除外", "polarity": "極性", "sign": "s", "n_pixels": "N",
    "raw_sum": "Σp（補正前）", "raw_mean": "平均 p", "bg_method_label": "背景法", "bg_value": "b（背景）",
    "bg_sum": "N×b", "corrected": "S（補正後）", "n_low_extreme": "値0の画素", "n_high_extreme": "最大値の画素",
    "warnings": "警告",
}
MEAS_SHOW = ["image_id", "band_id", "lane_id", "sample_id", "group", "bio_rep", "protein", "membrane_id", "exposure_id",
             "image_included", "excluded", "polarity", "sign", "n_pixels", "raw_sum", "bg_method_label", "bg_value",
             "bg_sum", "corrected", "n_low_extreme", "n_high_extreme", "warnings"]


# ===================================================================== 背景補正・定量
class QuantPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("背景補正・定量", "全画像のバンドの測定値を一覧します。値は ROI・背景・極性の設定から毎回再計算されます。"))
        self.hint = HintBox(
            "背景補正の方法で S は大きく変わります。<b>近傍背景</b>はバンドの真上・真下の帯（バンドと同じ幅）の平均、"
            "<b>手動背景ROI</b>は自分で置いた背景領域の平均、<b>補正なし</b>は暗いバンドなら白（最大値）を基準にします。"
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
        self.tbl_img = QTableWidget()
        cols = ["image_id", "タンパク質", "定量に使う", "極性", "背景法（既定）", "近傍：間隔 px", "近傍：高さ px", "手動背景ROI数", "バンド数"]
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
                for c, v in ((0, im.image_id), (1, im.protein_target), (7, str(len(im.bg_rois))), (8, str(len(im.bands)))):
                    it = QTableWidgetItem(v)
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    t.setItem(r, c, it)
                chk = QTableWidgetItem()
                chk.setFlags((chk.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
                chk.setCheckState(Qt.Checked if im.include else Qt.Unchecked)
                t.setItem(r, 2, chk)
                cmb = QComboBox()
                cmb.addItem("暗いバンド", POLARITY_DARK)
                cmb.addItem("明るいバンド", POLARITY_BRIGHT)
                cmb.setCurrentIndex(0 if im.polarity == POLARITY_DARK else 1)
                cmb.activated.connect(lambda _i, c=cmb, iid=im.image_id: self._set(iid, "polarity", c.currentData(), "バンド極性の変更"))
                t.setCellWidget(r, 3, cmb)
                cmb = QComboBox()
                for m in BG_METHODS:
                    cmb.addItem(BG_LABELS[m], m)
                cmb.setCurrentIndex(BG_METHODS.index(im.bg_method))
                cmb.activated.connect(lambda _i, c=cmb, iid=im.image_id: self._set(iid, "bg_method", c.currentData(), "背景推定法の変更"))
                t.setCellWidget(r, 4, cmb)
                for c, attr in ((5, "bg_gap"), (6, "bg_height")):
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
        if self._building or item.column() != 2:
            return
        iid = self.tbl_img.item(item.row(), 0).text()
        QTimer.singleShot(0, lambda: self._set(iid, "include", item.checkState() == Qt.Checked, "定量に使う画像の変更"))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "測定値 CSV", "measurements.csv", "CSV (*.csv)")
        if path:
            self.ctrl.measurements().to_csv(path, index=False, encoding="utf-8-sig")


# ===================================================================== 正規化
class NormPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("正規化", "同じ sample_id の Target バンドと Loading control（または total タンパク質）のバンドの比を計算します。"))
        self.hint = HintBox(
            "比 = S(Target) ÷ S(Loading control) は、<b>両方のバンドが線形範囲にある</b>ときだけロード量の違いを補正できます。"
            "Loading control が飽和していると、ロード量の差が比に正しく反映されません。"
            "phospho/total を見る場合は、分子に phospho 体、分母に total 体を選びます。", lesson=4)
        lay.addWidget(self.hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("分子（Target）："))
        self.cmb_num = QComboBox()
        row.addWidget(self.cmb_num)
        row.addWidget(QLabel("÷ 分母（Loading control / total）："))
        self.cmb_den = QComboBox()
        row.addWidget(self.cmb_den)
        row.addWidget(QLabel("基準群（Control）："))
        self.cmb_ctrl = QComboBox()
        row.addWidget(self.cmb_ctrl)
        row.addStretch(1)
        lay.addLayout(row)
        for cmb in (self.cmb_num, self.cmb_den, self.cmb_ctrl):
            cmb.setMinimumWidth(140)
            cmb.activated.connect(self._on_settings)
        steps = QLabel("<b>計算の段階</b><br>" + STEP_DESCRIPTION.replace("\n", "<br>"))
        steps.setStyleSheet("background:#F4F6FA; border:1px solid #C8D0E0; padding:6px;")
        lay.addWidget(steps)
        self.lbl_issues = QLabel()
        self.lbl_issues.setWordWrap(True)
        self.lbl_issues.setStyleSheet("color:#8a3000;")
        lay.addWidget(self.lbl_issues)
        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)
        g = QGroupBox("対応表（sample_id × タンパク質 → 画像/バンド）：空欄は対応するバンドが無いことを示します")
        v = QVBoxLayout(g)
        self.map_model = DataFrameModel()
        v.addWidget(make_table_view(self.map_model))
        split.addWidget(g)
        g = QGroupBox("① sample_id ごとの比")
        v = QVBoxLayout(g)
        self.s_model = DataFrameModel(headers={"numerator": "S(分子)", "denominator": "S(分母)", "ratio": "比",
                                               "fold": "fold（参考）", "status": "状態"})
        self.s_model.highlight = lambda r: (QColor("#FFF0C0") if self.s_model.frame().iloc[r]["status"] != "OK" else None)
        v.addWidget(make_table_view(self.s_model))
        split.addWidget(g)
        g = QGroupBox("②③ biological replicate ごとの値（統計・Figure にはこの表を使います）")
        v = QVBoxLayout(g)
        self.b_model = DataFrameModel(headers={"n_tech": "technical 数", "ratio": "比（tech 平均）", "fold": "fold to control"})
        v.addWidget(make_table_view(self.b_model))
        split.addWidget(g)
        chk = QLabel("<b>正規化比を使う前のチェック</b>：□ Target と Loading control の両方で飽和候補の警告を確認した　"
                     "□ 両者が線形範囲にあることを希釈系列等で確認した（未確認なら結果に注記）　"
                     "□ Loading control の量が処理で変わらないことを確認した　□ 比較する試料が同じ膜・同じ露光条件である")
        chk.setWordWrap(True)
        chk.setStyleSheet("color:#333;")
        lay.addWidget(chk)
        ctrl.changed.connect(self.request_refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def refresh(self) -> None:
        p = self.ctrl.project
        s = p.settings
        prots = p.proteins()
        for cmb, cur, items in ((self.cmb_num, s.numerator_protein, prots), (self.cmb_den, s.denominator_protein, prots),
                                (self.cmb_ctrl, s.control_group, p.groups())):
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItem("（選択）", "")
            for it in items:
                cmb.addItem(it, it)
            cmb.setCurrentIndex(max(cmb.findData(cur), 0))
            cmb.blockSignals(False)
        res = self.ctrl.normalization()
        self.lbl_issues.setText("\n".join(f"⚠ {i}" for i in res.issues))
        st = res.sample_table
        self.s_model.set_frame(st)
        self.b_model.set_frame(res.bio_table)
        m = self.ctrl.measurements()
        if len(m):
            use = m[m["image_included"].astype(bool) & (m["sample_id"] != "")]
            use = use.assign(where=use["image_id"] + "/" + use["band_id"] + use["excluded"].map({True: "（除外）", False: ""}))
            piv = use.groupby(["sample_id", "protein"])["where"].agg(", ".join).unstack(fill_value="")
            piv = piv.reset_index()
            self.map_model.set_frame(piv)
        else:
            self.map_model.set_frame(pd.DataFrame())

    def _on_settings(self) -> None:
        s = self.ctrl.project.settings
        new = (self.cmb_num.currentData() or "", self.cmb_den.currentData() or "", self.cmb_ctrl.currentData() or "")
        if new != (s.numerator_protein, s.denominator_protein, s.control_group):
            s.numerator_protein, s.denominator_protein, s.control_group = new
            self.ctrl.commit("正規化設定の変更", f"分子 {new[0]} ÷ 分母 {new[1]}、Control {new[2]}")


# ===================================================================== QC
class QCPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        self._issues = []
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("QC（品質管理）", "自動チェックで見つかった「疑い」の一覧です。確定診断ではありません。"
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


# ===================================================================== 統計
class StatsPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("統計", "biological replicate を独立な単位（n）として2群を比較します。technical replicate は平均済みで n に数えません。"))
        self.hint = HintBox(
            "<b>n はバンドの数ではなく、独立な生物学的反復の数</b>です。同じ試料を3レーン流しても n=1 です。"
            "独立した2群なら Welch の t 検定、同じ個体・同じ実験日で対応がある2群なら対応のある t 検定を使います。"
            "p 値だけでなく、差の大きさ（効果量）と 95% 信頼区間を見てください。", lesson=5)
        lay.addWidget(self.hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("値："))
        self.cmb_val = QComboBox()
        self.cmb_val.addItem("fold to control", "fold")
        self.cmb_val.addItem("比（ratio）", "ratio")
        row.addWidget(self.cmb_val)
        row.addWidget(QLabel("群 A："))
        self.cmb_a = QComboBox()
        row.addWidget(self.cmb_a)
        row.addWidget(QLabel("群 B："))
        self.cmb_b = QComboBox()
        row.addWidget(self.cmb_b)
        row.addWidget(QLabel("検定："))
        self.cmb_test = QComboBox()
        self.cmb_test.addItem("Welch の t 検定（独立2群）", "welch")
        self.cmb_test.addItem("対応のある t 検定（同じ bio_rep ID で対応）", "paired")
        for label in ("一元配置 ANOVA（3群以上）— Phase 2・未実装", "多重比較 — Phase 2・未実装",
                      "二要因 ANOVA／混合効果モデル — Phase 2・未実装"):
            self.cmb_test.addItem(label, "")
            self.cmb_test.model().item(self.cmb_test.count() - 1).setEnabled(False)
        row.addWidget(self.cmb_test)
        row.addStretch(1)
        lay.addLayout(row)
        for c in (self.cmb_val, self.cmb_a, self.cmb_b, self.cmb_test):
            c.setMinimumWidth(120)
            c.activated.connect(self._on_settings)
        g = QGroupBox("検定前の確認：解析単位（biological replicate）と群・画像・膜の割当て")
        v = QVBoxLayout(g)
        self.assign_model = DataFrameModel(headers={"n_tech": "technical 数", "fold": "fold", "ratio": "比"})
        v.addWidget(make_table_view(self.assign_model))
        self.chk_confirm = QCheckBox("上の表で、群・bio_rep・使用したバンド（画像/膜）の割当てが正しいことを確認した")
        self.chk_confirm.toggled.connect(self._render)
        v.addWidget(self.chk_confirm)
        lay.addWidget(g, 1)
        self.txt = QTextBrowser()
        lay.addWidget(self.txt, 2)
        ctrl.changed.connect(self.request_refresh)
        ctrl.projectReplaced.connect(lambda: self.chk_confirm.setChecked(False))

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def refresh(self) -> None:
        s = self.ctrl.project.settings
        res = self.ctrl.normalization()
        groups = list(dict.fromkeys(res.bio_table["group"])) if len(res.bio_table) else []
        for cmb, cur in ((self.cmb_a, s.stat_group_a), (self.cmb_b, s.stat_group_b)):
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItem("（選択）", "")
            for g in groups:
                cmb.addItem(g, g)
            cmb.setCurrentIndex(max(cmb.findData(cur), 0))
            cmb.blockSignals(False)
        self.cmb_val.setCurrentIndex(max(self.cmb_val.findData(s.stat_value), 0))
        self.cmb_test.setCurrentIndex(max(self.cmb_test.findData(s.stat_test), 0))
        bio = res.bio_table.copy()
        if len(bio):
            st = res.sample_table.set_index("sample_id")
            bio["使用バンド（分子 | 分母）"] = bio["sample_ids"].map(
                lambda ids: "; ".join(f"{st.loc[i, 'num_band']} | {st.loc[i, 'den_band']}" for i in ids.split(",") if i in st.index))
            bio["膜（分子/分母）"] = bio["sample_ids"].map(
                lambda ids: ", ".join(sorted({f"{st.loc[i, 'num_membrane']}/{st.loc[i, 'den_membrane']}" for i in ids.split(",") if i in st.index})))
        self.assign_model.set_frame(bio)
        self._render()

    def _on_settings(self) -> None:
        s = self.ctrl.project.settings
        new = (self.cmb_val.currentData() or "fold", self.cmb_a.currentData() or "", self.cmb_b.currentData() or "",
               self.cmb_test.currentData() or "welch")
        if new != (s.stat_value, s.stat_group_a, s.stat_group_b, s.stat_test):
            s.stat_value, s.stat_group_a, s.stat_group_b, s.stat_test = new
            self.ctrl.commit("統計設定の変更", f"値 {new[0]}、{new[1]} vs {new[2]}、{new[3]}")

    def _render(self) -> None:
        s = self.ctrl.project.settings
        res = self.ctrl.normalization()
        bio = res.bio_table
        val = s.stat_value
        h = []
        if val == "fold" and not math.isfinite(res.control_mean):
            h.append("<p style='color:#B00000'>fold が計算されていません（［正規化］で Control 群を選択）。値を「比（ratio）」にするか、Control 群を設定してください。</p>")
        h.append("<h3>記述統計（biological replicate 単位）</h3><table border='1' cellspacing='0' cellpadding='3'>"
                 "<tr><th>群</th><th>n</th><th>平均</th><th>SD</th><th>SEM</th><th>95%CI</th><th>中央値</th><th>最小–最大</th></tr>")
        for g in (list(dict.fromkeys(bio["group"])) if len(bio) else []):
            d = describe(bio.loc[bio["group"] == g, val], g)
            h.append(f"<tr><td>{g}</td><td>{d.n}</td><td>{fmt(d.mean)}</td><td>{fmt(d.sd)}</td><td>{fmt(d.sem)}</td>"
                     f"<td>{fmt(d.ci_low)} – {fmt(d.ci_high)}</td><td>{fmt(d.median)}</td><td>{fmt(d.minimum)} – {fmt(d.maximum)}</td></tr>")
        h.append("</table>")
        h.append("<h3>2群の検定</h3>")
        if not self.chk_confirm.isChecked():
            h.append("<p><b>検定結果を表示するには、上の割当て表を確認してチェックを入れてください。</b></p>")
        else:
            r = two_group_test(bio, val, s.stat_group_a, s.stat_group_b, s.stat_test)
            if not r.ok:
                h.append(f"<p style='color:#B00000'><b>検定しませんでした：</b>{r.refusal}</p>")
            else:
                h.append(f"<p><b>{r.test_name}</b>　{r.group_a}（n={r.n_a}） vs {r.group_b}（n={r.n_b}）"
                         + (f"、対の数 {r.n_pairs}" if s.stat_test == "paired" else "") + "</p>"
                         f"<ul><li>独立な単位：biological replicate（bio_rep）</li>"
                         f"<li>差（{r.group_b} − {r.group_a}）＝ {fmt(r.mean_diff)}、95% 信頼区間 [{fmt(r.ci_low)}, {fmt(r.ci_high)}]</li>"
                         f"<li>t = {fmt(r.statistic)}、自由度 = {fmt(r.df)}、p = {fmt(r.p_value)}（両側）</li>"
                         f"<li>効果量：{r.effect_name} = {fmt(r.effect_size)}</li>"
                         f"<li>欠損・除外された値：{r.n_missing}</li></ul>")
            if r.notes:
                h.append("<p><b>検定の前提・注意</b></p><ul>" + "".join(f"<li>{n}</li>" for n in r.notes) + "</ul>")
        h.append("<p style='color:#555'>3群以上の比較（ANOVA）と多重比較は Phase 2 で、まだ実装していません。"
                 "2群ずつ t 検定を繰り返すと偽陽性が増えるため、3群以上のデザインでは統計担当者に相談してください。</p>")
        self.txt.setHtml("".join(h))


# ===================================================================== Figure
class FigurePage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("Figure", "biological replicate ごとの値を群ごとに描きます。個々の点を必ず表示します。"))
        self.hint = HintBox(
            "棒グラフだけでは n やばらつきが隠れます。<b>個々の biological replicate を点で示す</b>のが誠実な図示です。"
            "エラーバーが SD か SEM かを凡例・図の説明に必ず書いてください（本アプリは SD）。"
            "バンド画像を図に使う場合は、原画像を変更せず、別膜・別露光の境界を隠さないでください。", lesson=7)
        lay.addWidget(self.hint)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)
        ctl = QWidget()
        form = QFormLayout(ctl)
        self.cmb_kind = QComboBox()
        for k, v in PLOT_KINDS.items():
            self.cmb_kind.addItem(v, k)
        form.addRow("図の種類", self.cmb_kind)
        self.cmb_val = QComboBox()
        self.cmb_val.addItem("fold to control", "fold")
        self.cmb_val.addItem("比（ratio）", "ratio")
        form.addRow("値", self.cmb_val)
        self.ed_title = QLineEdit()
        form.addRow("タイトル", self.ed_title)
        self.ed_ylabel = QLineEdit()
        form.addRow("y 軸ラベル", self.ed_ylabel)
        self.ed_xlabel = QLineEdit()
        form.addRow("x 軸ラベル", self.ed_xlabel)
        self.cmb_font = QComboBox()
        for f in japanese_font_candidates() + ["Arial", "Helvetica", "DejaVu Sans"]:
            self.cmb_font.addItem(f)
        form.addRow("フォント", self.cmb_font)
        self.sp_font = QDoubleSpinBox(); self.sp_font.setRange(5, 30); self.sp_font.setSingleStep(0.5)
        form.addRow("フォントサイズ", self.sp_font)
        self.ed_ymin = QLineEdit(); self.ed_ymin.setPlaceholderText("自動")
        self.ed_ymax = QLineEdit(); self.ed_ymax.setPlaceholderText("自動")
        hb = QHBoxLayout(); hb.addWidget(self.ed_ymin); hb.addWidget(QLabel("〜")); hb.addWidget(self.ed_ymax)
        form.addRow("y 軸範囲", hb)
        self.sp_w = QDoubleSpinBox(); self.sp_w.setRange(1, 20); self.sp_w.setSingleStep(0.25)
        self.sp_h = QDoubleSpinBox(); self.sp_h.setRange(1, 20); self.sp_h.setSingleStep(0.25)
        hb = QHBoxLayout(); hb.addWidget(self.sp_w); hb.addWidget(QLabel("×")); hb.addWidget(self.sp_h); hb.addWidget(QLabel("inch"))
        form.addRow("図のサイズ", hb)
        self.sp_dpi = QSpinBox(); self.sp_dpi.setRange(72, 1200)
        form.addRow("PNG 解像度 (dpi)", self.sp_dpi)
        self.chk_n = QCheckBox("群名の下に n を表示")
        form.addRow(self.chk_n)
        self.tbl_groups = QTableWidget()
        self.tbl_groups.setColumnCount(3)
        self.tbl_groups.setHorizontalHeaderLabels(["群", "表示名", "色"])
        self.tbl_groups.verticalHeader().setVisible(False)
        self.tbl_groups.itemChanged.connect(self._on_change)
        self.tbl_groups.cellDoubleClicked.connect(self._pick_color)
        form.addRow(QLabel("群の表示名・色（色はダブルクリックで変更）"))
        form.addRow(self.tbl_groups)
        hb = QHBoxLayout()
        b = QPushButton("↑"); b.clicked.connect(lambda: self._move(-1)); hb.addWidget(b)
        b = QPushButton("↓"); b.clicked.connect(lambda: self._move(1)); hb.addWidget(b)
        hb.addWidget(QLabel("群の並び順"))
        form.addRow(hb)
        hb = QHBoxLayout()
        for text, fn in (("PNG", lambda: self._save("png")), ("SVG", lambda: self._save("svg")), ("PDF", lambda: self._save("pdf")),
                         ("データ CSV/JSON", self._save_data)):
            b = QPushButton(f"{text} を保存…")
            b.clicked.connect(fn)
            hb.addWidget(b)
        form.addRow(hb)
        for w in (self.cmb_kind, self.cmb_val, self.cmb_font):
            w.activated.connect(self._on_change)
        for w in (self.ed_title, self.ed_ylabel, self.ed_xlabel, self.ed_ymin, self.ed_ymax):
            w.editingFinished.connect(self._on_change)
        for w in (self.sp_font, self.sp_w, self.sp_h, self.sp_dpi):
            w.editingFinished.connect(self._on_change)
        self.chk_n.toggled.connect(self._on_change)
        scroll = QScrollArea()
        scroll.setWidget(ctl)
        scroll.setWidgetResizable(True)
        split.addWidget(scroll)
        self.fig = Figure(figsize=(3, 3), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.addWidget(self.canvas, 1)
        self.lbl_note = QLabel()
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("color:#555;")
        wl.addWidget(self.lbl_note)
        split.addWidget(wrap)
        split.setStretchFactor(1, 2)
        ctrl.changed.connect(self.request_refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def style(self) -> FigureStyle:
        st = FigureStyle.from_dict(self.ctrl.project.settings.figure)
        if not st.font_family:
            fonts = japanese_font_candidates()
            st.font_family = fonts[0] if fonts else "DejaVu Sans"
        return st

    def refresh(self) -> None:
        st = self.style()
        bio = self.ctrl.normalization().bio_table
        groups = list(dict.fromkeys(bio["group"])) if len(bio) else []
        order = [g for g in st.group_order if g in groups] + [g for g in groups if g not in st.group_order]
        self._building = True
        try:
            self.cmb_kind.setCurrentIndex(max(self.cmb_kind.findData(st.kind), 0))
            self.cmb_val.setCurrentIndex(max(self.cmb_val.findData(st.value_col), 0))
            self.ed_title.setText(st.title)
            self.ed_ylabel.setText(st.ylabel)
            self.ed_xlabel.setText(st.xlabel)
            if self.cmb_font.findText(st.font_family) < 0:
                self.cmb_font.addItem(st.font_family)
            self.cmb_font.setCurrentText(st.font_family)
            self.sp_font.setValue(st.font_size)
            self.ed_ymin.setText("" if st.ymin is None else str(st.ymin))
            self.ed_ymax.setText("" if st.ymax is None else str(st.ymax))
            self.sp_w.setValue(st.width_in)
            self.sp_h.setValue(st.height_in)
            self.sp_dpi.setValue(st.dpi)
            self.chk_n.setChecked(st.show_n)
            self.tbl_groups.setRowCount(len(order))
            for r, g in enumerate(order):
                it = QTableWidgetItem(g)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.tbl_groups.setItem(r, 0, it)
                self.tbl_groups.setItem(r, 1, QTableWidgetItem(st.group_labels.get(g, g)))
                color = st.colors[r % len(st.colors)]
                it = QTableWidgetItem(color)
                it.setBackground(QColor(color))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.tbl_groups.setItem(r, 2, it)
        finally:
            self._building = False
        st.group_order = order
        no_fold = len(bio) > 0 and not bio["fold"].notna().any()
        if st.value_col == "fold" and no_fold:
            self.lbl_note.setText("⚠ fold が計算されていません。［正規化］で Control 群を選ぶか、値を「比」にしてください。")
        else:
            self.lbl_note.setText("点＝biological replicate（technical replicate は平均済み）。エラーバー＝SD。"
                                  "ジッター（点の横ずれ）は再現性のため固定乱数で配置しています。")
        plot_groups(bio, st, self.fig)
        self.canvas.draw_idle()

    def _collect(self) -> dict:
        st = self.style()
        def num(text):
            try:
                return float(text) if text.strip() else None
            except ValueError:
                return None
        st.kind = self.cmb_kind.currentData()
        st.value_col = self.cmb_val.currentData()
        st.title, st.ylabel, st.xlabel = self.ed_title.text(), self.ed_ylabel.text(), self.ed_xlabel.text()
        st.font_family = self.cmb_font.currentText()
        st.font_size = self.sp_font.value()
        st.ymin, st.ymax = num(self.ed_ymin.text()), num(self.ed_ymax.text())
        st.width_in, st.height_in, st.dpi = self.sp_w.value(), self.sp_h.value(), self.sp_dpi.value()
        st.show_n = self.chk_n.isChecked()
        order, labels, colors = [], {}, []
        for r in range(self.tbl_groups.rowCount()):
            g = self.tbl_groups.item(r, 0).text()
            order.append(g)
            labels[g] = self.tbl_groups.item(r, 1).text() if self.tbl_groups.item(r, 1) else g
            colors.append(self.tbl_groups.item(r, 2).text())
        st.group_order = order
        st.group_labels = {g: l for g, l in labels.items() if l != g}
        if colors:
            st.colors = colors + [c for c in st.colors[len(colors):]]
        return st.to_dict()

    def _on_change(self, *_a) -> None:
        if self._building:
            return
        new = self._collect()
        if new != self.ctrl.project.settings.figure:
            self.ctrl.project.settings.figure = new
            QTimer.singleShot(0, lambda: self.ctrl.commit("Figure 設定の変更", "", log=False))

    def _pick_color(self, row: int, col: int) -> None:
        if col != 2:
            return
        cur = QColor(self.tbl_groups.item(row, 2).text())
        c = QColorDialog.getColor(cur, self, "色を選択")
        if c.isValid():
            self.tbl_groups.item(row, 2).setText(c.name())
            self._on_change()

    def _move(self, d: int) -> None:
        r = self.tbl_groups.currentRow()
        st = self.style()
        order = [self.tbl_groups.item(i, 0).text() for i in range(self.tbl_groups.rowCount())]
        if r < 0 or not (0 <= r + d < len(order)):
            return
        order[r], order[r + d] = order[r + d], order[r]
        cols = [self.tbl_groups.item(i, 2).text() for i in range(len(order))]
        cols[r], cols[r + d] = cols[r + d], cols[r]
        st_d = self._collect()
        st_d["group_order"] = order
        st_d["colors"] = cols + st_d["colors"][len(cols):]
        self.ctrl.project.settings.figure = st_d
        self.ctrl.commit("Figure 群の並び順", " → ".join(order))
        self.tbl_groups.setCurrentCell(r + d, 0)

    def _save(self, ext: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, f"{ext.upper()} で保存", f"figure.{ext}", f"{ext.upper()} (*.{ext})")
        if not path:
            return
        st = self.style()
        fig = plot_groups(self.ctrl.normalization().bio_table, st, Figure(figsize=(st.width_in, st.height_in)))
        save_figure(fig, path, dpi=st.dpi)
        self.ctrl.project.log("Figure 保存", path)
        QMessageBox.information(self, "Figure", f"保存しました：{path}\n同じ場所に「データ CSV/JSON を保存」で数値と設定も残すことを推奨します。")

    def _save_data(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "図のデータ（CSV）", "figure_data.csv", "CSV (*.csv)")
        if not path:
            return
        from ..core.export import settings_dict
        res = self.ctrl.normalization()
        res.bio_table.to_csv(path, index=False, encoding="utf-8-sig")
        meta = settings_dict(self.ctrl.project)
        meta["figure_style"] = self.style().to_dict()
        meta["normalization_steps"] = STEP_DESCRIPTION
        meta["control_mean_ratio"] = res.control_mean
        Path(path).with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        QMessageBox.information(self, "データ保存", f"{path}\n{Path(path).with_suffix('.json')}\nを保存しました。")
