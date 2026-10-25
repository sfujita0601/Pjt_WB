"""［正規化］画面：タンパク質の役割、対応表、計算の段階、追跡情報。"""
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from ..core.normalize import STEP_DESCRIPTION
from ..models.project import PROTEIN_ROLES, ROLE_LABELS, ROLE_LOADING, ROLE_OTHER, ROLE_TOTAL
from .terms import TERMS
from .widgets import DataFrameModel, HintBox, LazyRefreshMixin, make_table_view, page_header

SAMPLE_SHOW = ["sample_id", "group", "bio_rep", "tech_rep", "subject_id", "numerator", "denominator", "ratio", "fold",
               "status", "caution", "num_band", "num_membrane", "num_exposure", "num_bit_depth",
               "den_band", "den_membrane", "den_exposure", "den_bit_depth"]
BIO_SHOW = ["group", "bio_rep", "subject_id", "n_tech", "ratio", "fold", "log2_ratio", "log2_fold", "fold_geo",
            "log2_status", "sample_ids"]


class NormPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("正規化（normalization）",
                                  "同じ試料ID（sample_id）の Target バンドを、ローディングコントロール（loading control）"
                                  "または総量（total）のバンドで割ります。画像が別でも試料IDで対応づけます。"))
        self.hint = HintBox(
            "比 = S(Target) ÷ S(ローディングコントロール) がロード量の違いを補正できるのは、<b>両方のバンドが線形範囲にある</b>ときだけです。"
            "phospho/total を見る場合は、分子にリン酸化体、分母に総量を選びます。"
            "<b>別の膜どうしの比</b>は対応付けはできても、膜ごとの転写・露光の違いは補正されないので、正規化として適切とは限りません。", lesson=4)
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("分子（Target / phospho）："))
        self.cmb_num = QComboBox()
        row.addWidget(self.cmb_num)
        row.addWidget(QLabel("÷ 分母（ローディングコントロール / total）："))
        self.cmb_den = QComboBox()
        row.addWidget(self.cmb_den)
        row.addWidget(QLabel("対照群（control）："))
        self.cmb_ctrl = QComboBox()
        row.addWidget(self.cmb_ctrl)
        row.addStretch(1)
        lay.addLayout(row)
        for cmb in (self.cmb_num, self.cmb_den, self.cmb_ctrl):
            cmb.setMinimumWidth(150)
            cmb.activated.connect(self._on_settings)
        steps = QLabel("<b>計算の段階</b><br>" + STEP_DESCRIPTION.replace("\n", "<br>"))
        steps.setWordWrap(True)
        steps.setStyleSheet("background:#F4F6FA; border:1px solid #C8D0E0; padding:6px;")
        lay.addWidget(steps)
        self.lbl_block = QLabel()
        self.lbl_block.setWordWrap(True)
        self.lbl_block.setStyleSheet("color:#B00000; font-weight:bold;")
        lay.addWidget(self.lbl_block)
        self.lbl_issues = QLabel()
        self.lbl_issues.setWordWrap(True)
        self.lbl_issues.setStyleSheet("color:#8a3000;")
        lay.addWidget(self.lbl_issues)

        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        # --- タンパク質の役割と対応表
        w = QWidget()
        v = QVBoxLayout(w)
        split = QSplitter(Qt.Horizontal)
        g = QGroupBox("タンパク質の役割（タンパク質ごとのバンド・画像の一覧）")
        gv = QVBoxLayout(g)
        self.tbl_roles = QTableWidget()
        self.tbl_roles.setColumnCount(5)
        self.tbl_roles.setHorizontalHeaderLabels(["タンパク質", "役割", "バンド数", "画像（膜・露光・bit）", "定量に使う画像"])
        self.tbl_roles.verticalHeader().setVisible(False)
        self.tbl_roles.horizontalHeader().setStretchLastSection(True)
        gv.addWidget(self.tbl_roles)
        split.addWidget(g)
        g = QGroupBox("対応表（試料ID × タンパク質 → 画像/バンド）：空欄は対応するバンドが無いことを示します")
        gv = QVBoxLayout(g)
        self.map_model = DataFrameModel()
        gv.addWidget(make_table_view(self.map_model))
        split.addWidget(g)
        v.addWidget(split)
        tabs.addTab(w, "タンパク質と対応表")
        # --- ① 試料ごと
        w = QWidget()
        v = QVBoxLayout(w)
        self.s_model = DataFrameModel(headers=TERMS)
        self.s_model.highlight = self._sample_color
        v.addWidget(make_table_view(self.s_model))
        tabs.addTab(w, "① 試料IDごとの比（追跡情報つき）")
        # --- ②③④ 生物学的反復ごと
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("統計と Figure にはこの表（生物学的反復ごと）を使います。検定に使う値は［統計］で選びます。"))
        self.b_model = DataFrameModel(headers={**TERMS, "ratio": "正規化比（技術的反復の平均）"})
        v.addWidget(make_table_view(self.b_model))
        tabs.addTab(w, "②③④ 生物学的反復ごとの値")
        self.tabs = tabs

        chk = QLabel("<b>正規化比を使う前のチェック</b>：□ Target とローディングコントロールの両方で飽和候補の警告を確認した　"
                     "□ 両者が線形範囲にあることを希釈系列等で確認した（未確認なら結果に注記）　"
                     "□ ローディングコントロールの量が処理で変わらないことを確認した　□ 比較する試料が同じ膜・同じ露光条件・同じ bit 深度である")
        chk.setWordWrap(True)
        lay.addWidget(chk)
        ctrl.changed.connect(self.request_refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def _sample_color(self, r: int):
        row = self.s_model.frame().iloc[r]
        if row["status"] != "OK":
            return QColor("#F8D0D0")
        if row.get("caution"):
            return QColor("#FFF0C0")
        return None

    def refresh(self) -> None:
        p = self.ctrl.project
        s = p.settings
        prots = p.proteins()
        roles = s.protein_roles
        num_choices = [x for x in prots if roles.get(x) not in (ROLE_LOADING, ROLE_TOTAL)] + \
                      [x for x in prots if roles.get(x) in (ROLE_LOADING, ROLE_TOTAL)]
        den_choices = [x for x in prots if roles.get(x) in (ROLE_LOADING, ROLE_TOTAL)] + \
                      [x for x in prots if roles.get(x) not in (ROLE_LOADING, ROLE_TOTAL)]
        for cmb, cur, items in ((self.cmb_num, s.numerator_protein, num_choices), (self.cmb_den, s.denominator_protein, den_choices),
                                (self.cmb_ctrl, s.control_group, p.groups())):
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItem("（選択）", "")
            for it in items:
                role = roles.get(it)
                cmb.addItem(f"{it}（{ROLE_LABELS[role].split('（')[0]}）" if role and cmb is not self.cmb_ctrl else it, it)
            cmb.setCurrentIndex(max(cmb.findData(cur), 0))
            cmb.blockSignals(False)
        self._fill_roles()
        res = self.ctrl.normalization()
        self.lbl_block.setText("\n".join(f"⛔ {b}" for b in res.blocking))
        self.lbl_issues.setText("\n".join(f"⚠ {i}" for i in res.issues))
        st = res.sample_table
        self.s_model.set_frame(st[SAMPLE_SHOW] if len(st) else pd.DataFrame(columns=SAMPLE_SHOW))
        bio = res.bio_table
        self.b_model.set_frame(bio[BIO_SHOW] if len(bio) else pd.DataFrame(columns=BIO_SHOW))
        m = self.ctrl.measurements()
        if len(m):
            use = m[m["image_included"].astype(bool) & (m["sample_id"] != "")]
            use = use.assign(where=use["image_id"] + "/" + use["band_id"] + use["excluded"].map({True: "（除外）", False: ""}))
            piv = use.groupby(["sample_id", "protein"])["where"].agg(", ".join).unstack(fill_value="").reset_index()
            piv.columns = [TERMS.get(c, c) if c == "sample_id" else f"{c}（{ROLE_LABELS[roles[c]].split('（')[0]}）"
                           if c in roles else c for c in piv.columns]
            self.map_model.set_frame(piv)
        else:
            self.map_model.set_frame(pd.DataFrame())

    def _fill_roles(self) -> None:
        p = self.ctrl.project
        roles = p.settings.protein_roles
        prots = p.proteins()
        self._building = True
        try:
            t = self.tbl_roles
            t.setRowCount(len(prots))
            for r, name in enumerate(prots):
                it = QTableWidgetItem(name)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, 0, it)
                cmb = QComboBox()
                for role in PROTEIN_ROLES:
                    cmb.addItem(ROLE_LABELS[role], role)
                cmb.setCurrentIndex(PROTEIN_ROLES.index(roles.get(name, ROLE_OTHER)))
                cmb.activated.connect(lambda _i, c=cmb, n=name: self._on_role(n, c.currentData()))
                t.setCellWidget(r, 1, cmb)
                bands = [(im, b) for im in p.images for b in im.bands if im.band_protein(b) == name]
                imgs = sorted({im.image_id for im, _b in bands})
                desc = [f"{iid}（{p.image(iid).membrane_id or '膜?'}・{p.image(iid).exposure_id or '露光?'}・{p.image(iid).bit_depth}bit）"
                        for iid in imgs]
                used = [iid for iid in imgs if p.image(iid).include]
                for c, v in ((2, str(len(bands))), (3, ", ".join(desc)), (4, ", ".join(used) or "（なし）")):
                    it = QTableWidgetItem(v)
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    t.setItem(r, c, it)
            t.resizeColumnsToContents()
        finally:
            self._building = False

    def _on_role(self, name: str, role: str) -> None:
        roles = self.ctrl.project.settings.protein_roles
        if roles.get(name) != role:
            roles[name] = role
            self.ctrl.commit("タンパク質の役割", f"{name}: {ROLE_LABELS[role]}")

    def _on_settings(self) -> None:
        s = self.ctrl.project.settings
        new = (self.cmb_num.currentData() or "", self.cmb_den.currentData() or "", self.cmb_ctrl.currentData() or "")
        if new != (s.numerator_protein, s.denominator_protein, s.control_group):
            s.numerator_protein, s.denominator_protein, s.control_group = new
            self.ctrl.commit("正規化設定の変更", f"分子 {new[0]} ÷ 分母 {new[1]}、対照群 {new[2]}")
