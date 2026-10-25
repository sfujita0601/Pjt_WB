"""［Figure］画面：定量グラフと、バンド画像パネルとの複合 Figure。"""
from __future__ import annotations

import json
from pathlib import Path

import tifffile
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core.bandpanel import composite_figure
from ..core.export import settings_dict
from ..core.figure import ERROR_BARS, PLOT_KINDS, VALUE_LABELS, FigureStyle, figure_caption, japanese_font_candidates, plot_groups
from ..core.normalize import STEP_DESCRIPTION
from ..core.stats_runner import run_stats, value_column
from ..models.project import ROLE_LOADING, ROLE_TOTAL
from .widgets import HintBox, LazyRefreshMixin, page_header


class FigurePage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        self._panel_rows = []
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("Figure", "生物学的反復ごとの値を群ごとに描きます。個々の点を必ず表示します。"))
        self.hint = HintBox(
            "推奨は<b>個々の生物学的反復を点で示し、平均 ± SD</b>を重ねる図です。"
            "<b>SD</b>はデータのばらつき、<b>SEM</b>は平均の推定精度（n が増えると小さくなる）、<b>95%CI</b>は平均の区間推定です。"
            "どれを使ったかを図の説明に必ず書いてください（下の説明文に自動で入ります）。"
            "バンド画像は元画像から連続した領域を切り出し、並べ替え・継ぎ合わせはしません。", lesson=7)
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
        for k, v in VALUE_LABELS.items():
            self.cmb_val.addItem(v, k)
        form.addRow("縦軸の値", self.cmb_val)
        self.cmb_err = QComboBox()
        for k, v in ERROR_BARS.items():
            self.cmb_err.addItem(v, k)
        form.addRow("エラーバー", self.cmb_err)
        self.chk_stats = QCheckBox("［統計］の結果（調整済み p 値）を図に表示")
        form.addRow(self.chk_stats)
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
        form.addRow("グラフのサイズ", hb)
        self.sp_dpi = QSpinBox(); self.sp_dpi.setRange(72, 1200)
        form.addRow("PNG 解像度 (dpi)", self.sp_dpi)
        self.chk_n = QCheckBox("群名の下に n を表示")
        form.addRow(self.chk_n)
        self.tbl_groups = QTableWidget()
        self.tbl_groups.setColumnCount(3)
        self.tbl_groups.setHorizontalHeaderLabels(["群", "表示名", "色"])
        self.tbl_groups.verticalHeader().setVisible(False)
        self.tbl_groups.setMaximumHeight(150)
        self.tbl_groups.itemChanged.connect(self._on_change)
        self.tbl_groups.cellDoubleClicked.connect(self._pick_color)
        form.addRow(QLabel("群の表示名・色（色はダブルクリックで変更）"))
        form.addRow(self.tbl_groups)
        hb = QHBoxLayout()
        b = QPushButton("↑"); b.clicked.connect(lambda: self._move(-1)); hb.addWidget(b)
        b = QPushButton("↓"); b.clicked.connect(lambda: self._move(1)); hb.addWidget(b)
        hb.addWidget(QLabel("群の並び順"))
        form.addRow(hb)

        form.addRow(QLabel("<b>バンド画像パネル（複合 Figure）</b>"))
        self.lst_imgs = QListWidget()
        self.lst_imgs.setMaximumHeight(110)
        self.lst_imgs.itemChanged.connect(lambda _it: self._render())
        form.addRow("表示する画像", self.lst_imgs)
        self.sp_margin = QSpinBox(); self.sp_margin.setRange(0, 500); self.sp_margin.setValue(10)
        self.sp_margin.valueChanged.connect(lambda _v: self._render())
        form.addRow("バンド上下の余白 (px)", self.sp_margin)
        self.cmb_levels = QComboBox()
        self.cmb_levels.addItem("切り出し範囲の最小〜最大で線形表示", "crop_minmax")
        self.cmb_levels.addItem("原画像の全範囲（0〜最大値）で線形表示", "full_range")
        self.cmb_levels.currentIndexChanged.connect(lambda _i: self._render())
        form.addRow("表示レベル", self.cmb_levels)
        self.chk_rois = QCheckBox("測定領域（ROI）の枠を重ねる")
        self.chk_rois.toggled.connect(lambda _on: self._render())
        form.addRow(self.chk_rois)

        hb = QHBoxLayout()
        for text, fn in (("PNG", lambda: self._save("png")), ("SVG", lambda: self._save("svg")), ("PDF", lambda: self._save("pdf"))):
            b = QPushButton(f"{text} を保存…")
            b.clicked.connect(fn)
            hb.addWidget(b)
        form.addRow(hb)
        b = QPushButton("図のデータ・説明文・設定を保存（CSV/JSON/TXT）…")
        b.clicked.connect(self._save_data)
        form.addRow(b)
        for w in (self.cmb_kind, self.cmb_val, self.cmb_err, self.cmb_font):
            w.activated.connect(self._on_change)
        for w in (self.ed_title, self.ed_ylabel, self.ed_xlabel, self.ed_ymin, self.ed_ymax):
            w.editingFinished.connect(self._on_change)
        for w in (self.sp_font, self.sp_w, self.sp_h, self.sp_dpi):
            w.editingFinished.connect(self._on_change)
        self.chk_n.toggled.connect(self._on_change)
        self.chk_stats.toggled.connect(self._on_change)
        scroll = QScrollArea()
        scroll.setWidget(ctl)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(420)
        split.addWidget(scroll)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.tabs = QTabWidget()
        self.fig = Figure(figsize=(3, 3), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.tabs.addTab(self.canvas, "定量グラフ")
        self.fig2 = Figure(figsize=(4, 5), dpi=100)
        self.canvas2 = FigureCanvasQTAgg(self.fig2)
        self.tabs.addTab(self.canvas2, "バンド画像＋グラフ（複合）")
        self.tabs.currentChanged.connect(lambda _i: self._render())
        rl.addWidget(self.tabs, 1)
        self.lbl_warn = QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color:#B00000;")
        rl.addWidget(self.lbl_warn)
        rl.addWidget(QLabel("<b>図の説明文（自動生成・コピーして編集してください）</b>"))
        self.txt_caption = QPlainTextEdit()
        self.txt_caption.setReadOnly(True)
        self.txt_caption.setMaximumHeight(110)
        rl.addWidget(self.txt_caption)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        ctrl.changed.connect(self.request_refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    # ------------------------------------------------------------ スタイル
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
            self.cmb_err.setCurrentIndex(max(self.cmb_err.findData(st.error_bar), 0))
            self.chk_stats.setChecked(st.show_stats)
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
            self._fill_image_list()
        finally:
            self._building = False
        self._render()

    def _fill_image_list(self) -> None:
        p = self.ctrl.project
        prev = {self.lst_imgs.item(i).data(Qt.UserRole): self.lst_imgs.item(i).checkState()
                for i in range(self.lst_imgs.count())}
        roles = p.settings.protein_roles
        imgs = [im for im in p.images if im.bands]
        # Target を上、ローディングコントロール・total を下に並べる
        imgs.sort(key=lambda im: (roles.get(im.protein_target) in (ROLE_LOADING, ROLE_TOTAL), im.image_id))
        self.lst_imgs.clear()
        for im in imgs:
            it = QListWidgetItem(f"{im.image_id} {im.protein_target}（膜 {im.membrane_id or '—'}・露光 {im.exposure_id or '—'}）"
                                 + ("" if im.include else "［定量に使わない画像］"))
            it.setData(Qt.UserRole, im.image_id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(prev.get(im.image_id, Qt.Checked if im.include else Qt.Unchecked))
            self.lst_imgs.addItem(it)

    def _selected_images(self) -> list[str]:
        return [self.lst_imgs.item(i).data(Qt.UserRole) for i in range(self.lst_imgs.count())
                if self.lst_imgs.item(i).checkState() == Qt.Checked]

    def _stats(self):
        return run_stats(self.ctrl.project.settings, self.ctrl.normalization())

    def _render(self) -> None:
        if self._building:
            return
        st = self.style()
        res = self.ctrl.normalization()
        bio = res.bio_table
        warns = []
        if res.blocking:
            warns.append("⛔ " + " ".join(res.blocking))
            bio = bio.iloc[0:0]
        if len(bio) and not bio[st.value_col].notna().any():
            warns.append(f"「{VALUE_LABELS[st.value_col]}」が計算されていません（対照群の未選択、または log2 変換できない値）。［正規化］を確認してください。")
        out = self._stats()
        annotations = None
        if st.show_stats:
            if out.ok:
                annotations = out.annotations
                fig_log = st.value_col.startswith("log2")
                stat_log = value_column(self.ctrl.project.settings) == "log2_ratio"
                if fig_log != stat_log:
                    warns.append("図の縦軸と検定に使った値のスケール（線形／log2）が異なります。説明文で検定に使った値を明記しています。")
            else:
                warns.append(f"p 値を表示できません：{out.refusal or '［統計］で設定を確認してください。'}")
        self.lbl_warn.setText("\n".join(warns))
        norm_desc = (f"正規化：{self.ctrl.project.settings.numerator_protein} ÷ {self.ctrl.project.settings.denominator_protein}"
                     f"（同じ試料のバンドの背景補正後積分値の比）、対照群 {self.ctrl.project.settings.control_group or '未設定'}。")
        stats_desc = out.description if (out.ok and st.show_stats) else ""
        self.txt_caption.setPlainText(figure_caption(bio, st, stats_desc, norm_desc))
        if self.tabs.currentIndex() == 0:
            plot_groups(bio, st, self.fig, annotations)
            self.canvas.draw_idle()
        else:
            data = {iid: d for iid in self._selected_images() if (d := self.ctrl.image_data(iid)) is not None}
            _fig, self._panel_rows = composite_figure(self.ctrl.project, data, list(data), bio, st, annotations,
                                                      self.sp_margin.value(), self.cmb_levels.currentData(),
                                                      self.chk_rois.isChecked(), fig=self.fig2, resize=False)
            self.canvas2.draw_idle()

    def current_figure(self) -> Figure:
        st = self.style()
        bio = self.ctrl.normalization().bio_table
        out = self._stats()
        ann = out.annotations if (st.show_stats and out.ok) else None
        if self.tabs.currentIndex() == 0:
            return plot_groups(bio, st, Figure(figsize=(st.width_in, st.height_in)), ann)
        data = {iid: d for iid in self._selected_images() if (d := self.ctrl.image_data(iid)) is not None}
        fig, self._panel_rows = composite_figure(self.ctrl.project, data, list(data), bio, st, ann,
                                                 self.sp_margin.value(), self.cmb_levels.currentData(), self.chk_rois.isChecked())
        return fig

    # ------------------------------------------------------------ 設定の変更
    def _collect(self) -> dict:
        st = self.style()

        def num(text):
            try:
                return float(text) if text.strip() else None
            except ValueError:
                return None
        old_val = st.value_col
        st.kind = self.cmb_kind.currentData()
        st.value_col = self.cmb_val.currentData()
        st.error_bar = self.cmb_err.currentData()
        st.show_stats = self.chk_stats.isChecked()
        st.title, st.ylabel, st.xlabel = self.ed_title.text(), self.ed_ylabel.text(), self.ed_xlabel.text()
        if st.value_col != old_val and st.ylabel in VALUE_LABELS.values():   # 既定のラベルなら値に合わせて変える
            st.ylabel = VALUE_LABELS[st.value_col]
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
            st.colors = colors + st.colors[len(colors):]
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
        c = QColorDialog.getColor(QColor(self.tbl_groups.item(row, 2).text()), self, "色を選択")
        if c.isValid():
            self.tbl_groups.item(row, 2).setText(c.name())
            self._on_change()

    def _move(self, d: int) -> None:
        r = self.tbl_groups.currentRow()
        order = [self.tbl_groups.item(i, 0).text() for i in range(self.tbl_groups.rowCount())]
        if r < 0 or not (0 <= r + d < len(order)):
            return
        cols = [self.tbl_groups.item(i, 2).text() for i in range(len(order))]
        order[r], order[r + d] = order[r + d], order[r]
        cols[r], cols[r + d] = cols[r + d], cols[r]
        st_d = self._collect()
        st_d["group_order"] = order
        st_d["colors"] = cols + st_d["colors"][len(cols):]
        self.ctrl.project.settings.figure = st_d
        self.ctrl.commit("Figure 群の並び順", " → ".join(order))
        self.tbl_groups.setCurrentCell(r + d, 0)

    # ------------------------------------------------------------ 保存
    def _save(self, ext: str) -> None:
        name = "figure" if self.tabs.currentIndex() == 0 else "figure_with_bands"
        path, _ = QFileDialog.getSaveFileName(self, f"{ext.upper()} で保存", f"{name}.{ext}", f"{ext.upper()} (*.{ext})")
        if not path:
            return
        fig = self.current_figure()
        fig.savefig(path, dpi=self.style().dpi, bbox_inches="tight")
        self.ctrl.project.log("Figure 保存", path)
        extra = ""
        if self.tabs.currentIndex() == 1:
            self._save_panel_record(Path(path))
            extra = "\n切り出し範囲・表示レベル・元画像のハッシュを *_bands.json に、切り出した原画素を *_crop_*.tif に保存しました。"
        QMessageBox.information(self, "Figure", f"保存しました：{path}{extra}\n「図のデータ・説明文・設定を保存」で数値と説明文も残してください。")

    def _save_panel_record(self, path: Path) -> None:
        recs = []
        for row in self._panel_rows:
            crop_path = path.with_name(f"{path.stem}_crop_{row.image_id}.tif")
            tifffile.imwrite(crop_path, row.pixels, description=f"crop of {row.image_id} sha256={row.sha256} xywh={row.crop.as_tuple()}")
            rec = row.record()
            rec["crop_file"] = crop_path.name
            recs.append(rec)
        meta = {"note": "元画像の連続した領域を切り出し、線形の階調変換のみで表示（レーンの並べ替え・継ぎ合わせなし）。原画像ファイルは変更していない。",
                "rows": recs}
        path.with_name(f"{path.stem}_bands.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.ctrl.project.log("バンド画像パネルの記録", f"{path.stem}_bands.json（{len(recs)} 画像）")

    def _save_data(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "図のデータ（CSV）", "figure_data.csv", "CSV (*.csv)")
        if not path:
            return
        res = self.ctrl.normalization()
        res.bio_table.to_csv(path, index=False, encoding="utf-8-sig")
        p = Path(path)
        out = run_stats(self.ctrl.project.settings, res)
        if out.ok:
            out.table.to_csv(p.with_name(p.stem + "_stats.csv"), index=False, encoding="utf-8-sig")
        p.with_name(p.stem + "_caption.txt").write_text(self.txt_caption.toPlainText() + "\n", encoding="utf-8")
        meta = settings_dict(self.ctrl.project)
        meta["figure_style"] = self.style().to_dict()
        meta["normalization_steps"] = STEP_DESCRIPTION
        meta["control_mean_ratio"] = res.control_mean
        meta["control_mean_log2_ratio"] = res.control_log2_mean
        meta["stats"] = {"ok": out.ok, "method": out.method, "refusal": out.refusal, "description": out.description,
                         "notes": out.notes}
        meta["caption"] = self.txt_caption.toPlainText()
        p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        QMessageBox.information(self, "データ保存", "図の数値（CSV）、統計結果（_stats.csv）、説明文（_caption.txt）、設定（JSON）を保存しました。")

