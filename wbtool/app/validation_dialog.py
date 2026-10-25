"""Fiji/ImageJ 照合のダイアログ。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QTextBrowser,
    QVBoxLayout,
)

from ..core.validation import compare_normalization, compare_with_fiji, imagej_macro, read_fiji_results, report_markdown
from .widgets import DataFrameModel, make_table_view

HEADERS = {"wbtool_id": "ROI", "kind": "種類", "area_app": "面積（本アプリ）", "area_fiji": "Area（Fiji）",
           "mean_app": "平均（本アプリ）", "mean_fiji": "Mean（Fiji）", "rawintden_app": "Σp（本アプリ）",
           "rawintden_fiji": "RawIntDen（Fiji）", "match": "一致", "diagnosis": "判定・原因の候補",
           "bg_app": "b（本アプリ）", "bg_fiji": "b（Fiji から再計算）", "S_app": "S（本アプリ）", "S_fiji": "S（Fiji から再計算）",
           "S_diff": "差", "bg_method": "背景法"}


class ValidationDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.reports = {}
        self.setWindowTitle("Fiji/ImageJ との照合")
        self.resize(1100, 700)
        lay = QVBoxLayout(self)
        info = QLabel(
            "<b>手順</b>：① 画像を選び「マクロを保存」→ ② Fiji で画像を開かずに Plugins › Macros › Run… でマクロを実行"
            "（マクロが画像を開き、同じ ROI を測って Results を CSV に保存します）→ ③「Fiji の結果 CSV を読み込む」。<br>"
            "比較するのは同じ定義の量（Area・Mean・RawIntDen と、そこから同じ式で求めた b・S、正規化比・fold）です。"
            "Gel Analyzer のピーク面積は定義が違うため比較対象にしません。")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("画像："))
        self.cmb = QComboBox()
        for im in ctrl.project.images:
            if im.bands:
                self.cmb.addItem(f"{im.image_id} {im.protein_target}", im.image_id)
        row.addWidget(self.cmb)
        for text, fn in (("① マクロを保存…", self._save_macro), ("③ Fiji の結果 CSV を読み込む…", self._load_csv),
                         ("照合結果を Markdown で保存…", self._save_report)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.tabs = QTabWidget()
        self.roi_model = DataFrameModel(headers=HEADERS)
        self.band_model = DataFrameModel(headers=HEADERS)
        self.norm_model = DataFrameModel()
        for m in (self.roi_model, self.band_model, self.norm_model):
            m.highlight = (lambda mm: (lambda r: None if bool(mm.frame().iloc[r].get("match", True)) else QColor("#F8D0D0")))(m)
        self.tabs.addTab(make_table_view(self.roi_model), "ROI ごと")
        self.tabs.addTab(make_table_view(self.band_model), "バンドごとの b と S")
        self.tabs.addTab(make_table_view(self.norm_model), "正規化比・fold")
        lay.addWidget(self.tabs, 1)

    def _iid(self) -> str:
        return self.cmb.currentData() or ""

    def _save_macro(self) -> None:
        iid = self._iid()
        if not iid:
            return
        im = self.ctrl.project.image(iid)
        path, _ = QFileDialog.getSaveFileName(self, "ImageJ マクロを保存", f"wbtool_validate_{iid}.ijm", "ImageJ macro (*.ijm)")
        if not path:
            return
        results = str(Path(path).with_name(f"fiji_results_{iid}.csv"))
        Path(path).write_text(imagej_macro(self.ctrl.project, iid, results), encoding="utf-8")
        self.ctrl.project.log("Fiji 照合マクロを出力", f"{iid}: {path}")
        QMessageBox.information(self, "マクロを保存", f"{path}\n\nFiji で実行すると、結果が次に保存されます：\n{results}\n\n"
                                f"注意：画像 {Path(im.path).name} に Edit›Invert や 8-bit 変換をしないでください。")

    def _load_csv(self) -> None:
        iid = self._iid()
        if not iid:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Fiji の Results CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            fiji = read_fiji_results(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "読込失敗", f"{e}\n本アプリが出力したマクロで測定した Results を選んでください。")
            return
        data = self.ctrl.image_data(iid)
        rep = compare_with_fiji(self.ctrl.project, iid, data, fiji, self.ctrl.measurements())
        self.reports[iid] = rep
        self.roi_model.set_frame(rep.roi_table)
        self.band_model.set_frame(rep.band_table)
        if len(self.reports) >= 1:
            self.norm_model.set_frame(compare_normalization(self.ctrl.project, self.ctrl.measurements(), list(self.reports.values())))
        n_bad = int((~rep.roi_table["match"]).sum())
        self.lbl.setText(("<span style='color:#106010'><b>すべて一致しました。</b></span>" if rep.all_match else
                          f"<span style='color:#B00000'><b>一致しない ROI が {n_bad} 個あります。</b>「判定・原因の候補」列を確認してください。</span>")
                         + "<br>" + "<br>".join(rep.notes)
                         + "<br>正規化比・fold の比較は、読み込んだ画像の値だけ Fiji 由来に置き換え、ほかは本アプリの値で計算しています。")
        self.ctrl.project.log("Fiji 照合", f"{iid}: {path} → {'一致' if rep.all_match else f'不一致 {n_bad} ROI'}")

    def _save_report(self) -> None:
        if not self.reports:
            QMessageBox.information(self, "保存", "まだ照合結果がありません。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "照合結果", "fiji_validation.md", "Markdown (*.md)")
        if path:
            norm = compare_normalization(self.ctrl.project, self.ctrl.measurements(), list(self.reports.values()))
            Path(path).write_text(report_markdown(self.ctrl.project, list(self.reports.values()), norm), encoding="utf-8")
            self.ctrl.project.log("Fiji 照合結果を保存", path)
