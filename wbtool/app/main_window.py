"""メインウィンドウ：左サイドバー＋ページ、メニュー、実務／学習モード。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QStackedWidget, QToolBar, QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.examples import build_example_project
from ..core.export import export_all
from .controller import ProjectController
from .learn_page import LearnPage
from .pages_analysis import QCPage, QuantPage
from .pages_figure import FigurePage
from .pages_norm import NormPage
from .pages_stats import StatsPage
from .pages_data import ImagesPage, ProjectPage
from .roi_page import RoiPage
from .progress_panel import ProgressPanel
from .widgets import HintBox, error_box

PROJECT_FILTER = "Western blot プロジェクト (*.wbproj.json *.json)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ctrl = ProjectController()
        self.settings = QSettings("wbtool", "wbtool")
        self.resize(1440, 900)

        self.pages = {
            "project": ("プロジェクト", ProjectPage(self.ctrl)),
            "images": ("画像・試料情報", ImagesPage(self.ctrl)),
            "roi": ("レーン・バンド（ROI）", RoiPage(self.ctrl)),
            "quant": ("背景補正・定量", QuantPage(self.ctrl)),
            "norm": ("正規化", NormPage(self.ctrl)),
            "qc": ("QC", QCPage(self.ctrl)),
            "stats": ("統計", StatsPage(self.ctrl)),
            "figure": ("Figure", FigurePage(self.ctrl)),
            "learn": ("学習・ヘルプ", LearnPage()),
        }
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(4, 4, 4, 4)
        self.side = QListWidget()
        self.side.setMinimumHeight(330)
        self.side.setStyleSheet("QListWidget { font-size: 14px; } QListWidget::item { padding: 8px 6px; }"
                                "QListWidget::item:selected { background: #2F6FB0; color: white; }")
        self.stack = QStackedWidget()
        for key, (title, page) in self.pages.items():
            self.side.addItem(title)
            self.stack.addWidget(page)
            for hint in page.findChildren(HintBox):
                hint.openLesson.connect(self.open_lesson)
        self.side.currentRowChanged.connect(self.stack.setCurrentIndex)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.side, 3)
        self.progress = ProgressPanel(self.ctrl)
        self.progress.goto.connect(self.goto)
        ll.addWidget(self.progress, 2)
        left.setFixedWidth(200)
        lay.addWidget(left)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        p = self.pages
        p["project"][1].requestNew.connect(self.new_project)
        p["project"][1].requestOpen.connect(self.open_project)
        p["project"][1].requestSave.connect(self.save_project)
        p["project"][1].requestSaveAs.connect(self.save_project_as)
        p["project"][1].requestExample.connect(self.open_example)
        p["project"][1].requestExport.connect(self.export_results)
        p["images"][1].openRoi.connect(self.open_roi)

        self._build_menu()
        self.ctrl.stateChanged.connect(self._update_title)
        self.set_mode(self.settings.value("mode", "learn") == "learn")
        self.side.setCurrentRow(0)
        self.ctrl.new_project()

    # ------------------------------------------------------------ メニュー・ツールバー
    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("ファイル")
        for text, key, fn in (("新規プロジェクト", QKeySequence.New, self.new_project),
                              ("開く…", QKeySequence.Open, self.open_project),
                              ("保存", QKeySequence.Save, self.save_project),
                              ("名前を付けて保存…", QKeySequence.SaveAs, self.save_project_as),
                              (None, None, None),
                              ("教材用サンプルを開く", None, self.open_example),
                              ("結果を一括出力…", None, self.export_results),
                              (None, None, None),
                              ("終了", QKeySequence.Quit, self.close)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, self)
            if key:
                a.setShortcut(key)
            a.triggered.connect(fn)
            m.addAction(a)
        m = mb.addMenu("編集")
        self.act_undo = QAction("元に戻す", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(self.ctrl.undo)
        self.act_redo = QAction("やり直し", self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.triggered.connect(self.ctrl.redo)
        m.addAction(self.act_undo)
        m.addAction(self.act_redo)
        m = mb.addMenu("ヘルプ")
        a = QAction("操作マニュアル", self)
        a.triggered.connect(self.open_manual)
        m.addAction(a)
        a = QAction("このアプリについて", self)
        a.triggered.connect(self.about)
        m.addAction(a)

        tb = QToolBar("モード")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)
        tb.addWidget(QLabel(" モード： "))
        grp = QActionGroup(self)
        self.act_work = QAction("実務モード", self, checkable=True)
        self.act_learn = QAction("学習モード", self, checkable=True)
        for a in (self.act_work, self.act_learn):
            grp.addAction(a)
            tb.addAction(a)
        self.act_work.triggered.connect(lambda: self.set_mode(False))
        self.act_learn.triggered.connect(lambda: self.set_mode(True))
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        self.lbl_state = QLabel()
        tb.addWidget(self.lbl_state)

    def set_mode(self, learning: bool) -> None:
        self.act_learn.setChecked(learning)
        self.act_work.setChecked(not learning)
        for _t, page in self.pages.values():
            if hasattr(page, "set_learning"):
                page.set_learning(learning)
        self.settings.setValue("mode", "learn" if learning else "work")
        self.statusBar().showMessage(
            "学習モード：各画面に解説と教材へのリンクを表示します（計算は実務モードと同じです）。" if learning
            else "実務モード：解説を隠して表示を簡潔にします（計算は学習モードと同じです）。", 6000)

    def _update_title(self) -> None:
        name = self.ctrl.project.name
        path = self.ctrl.path.name if self.ctrl.path else "未保存"
        self.setWindowTitle(f"{'● ' if self.ctrl.dirty else ''}{name} — {path} — Western blot 定量ツール {__version__}")
        self.act_undo.setEnabled(self.ctrl.can_undo())
        self.act_redo.setEnabled(self.ctrl.can_redo())
        self.act_undo.setText(f"元に戻す：{self.ctrl.undo_label()}" if self.ctrl.can_undo() else "元に戻す")
        self.act_redo.setText(f"やり直し：{self.ctrl.redo_label()}" if self.ctrl.can_redo() else "やり直し")
        self.lbl_state.setText("● 未保存の変更あり" if self.ctrl.dirty else "保存済み")

    # ------------------------------------------------------------ 画面遷移
    def goto(self, key: str) -> None:
        self.side.setCurrentRow(list(self.pages).index(key))

    def open_lesson(self, n: int) -> None:
        self.goto("learn")
        self.pages["learn"][1].open_lesson(n)

    def open_manual(self) -> None:
        self.goto("learn")
        self.pages["learn"][1].open_manual()

    def open_roi(self, image_id: str) -> None:
        self.goto("roi")
        self.pages["roi"][1].show_image(image_id)

    # ------------------------------------------------------------ ファイル操作
    def _confirm_discard(self) -> bool:
        if not self.ctrl.dirty:
            return True
        r = QMessageBox.question(self, "未保存の変更", "未保存の変更があります。保存しますか？",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self) -> None:
        if self._confirm_discard():
            self.ctrl.new_project()
            self.goto("project")

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", self.settings.value("last_dir", ""), PROJECT_FILTER)
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> None:
        try:
            msgs = self.ctrl.load(path)
        except Exception as e:  # noqa: BLE001
            error_box(self, "プロジェクトを開けません", f"{path}<br>{e}",
                      "このアプリで保存したプロジェクトファイル（.wbproj.json）か確認してください。")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.goto("project")
        if msgs:
            QMessageBox.information(self, "読込時の確認", "\n\n".join(msgs))

    def save_project(self) -> bool:
        if self.ctrl.path is None:
            return self.save_project_as()
        try:
            self.ctrl.save()
        except Exception as e:  # noqa: BLE001
            error_box(self, "保存できません", str(e), "保存先フォルダーに書き込み権限があるか、ディスクの空きを確認してください。")
            return False
        self.statusBar().showMessage(f"保存しました：{self.ctrl.path}", 5000)
        return True

    def save_project_as(self) -> bool:
        default = str(Path(self.settings.value("last_dir", "")) / f"{self.ctrl.project.name}.wbproj.json")
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", default, PROJECT_FILTER)
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".wbproj.json"
        self.ctrl.path = Path(path)
        self.settings.setValue("last_dir", str(Path(path).parent))
        return self.save_project()

    def open_example(self) -> None:
        if not self._confirm_discard():
            return
        box = QMessageBox(self)
        box.setWindowTitle("教材用サンプル")
        box.setText("どちらのサンプルを開きますか？（どちらも教材用合成データで、実験由来ではありません）")
        b2 = box.addButton("2群（Control / Treated、過露光の例つき）", QMessageBox.AcceptRole)
        b3 = box.addButton("3群（Control / DrugA / DrugB、多重比較の練習）", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (b2, b3):
            return
        kind = "3groups" if box.clickedButton() is b3 else "2groups"
        folder = QFileDialog.getExistingDirectory(
            self, "教材用サンプル画像を書き出すフォルダーを選択（既存の同名ファイルはそのまま使います）",
            self.settings.value("last_dir", str(Path.home())))
        if not folder:
            return
        try:
            proj = build_example_project(Path(folder) / "wbtool_example", kind=kind)
        except Exception as e:  # noqa: BLE001
            error_box(self, "サンプルを作れません", str(e), "書き込み可能なフォルダーを選んでください。")
            return
        self.ctrl.new_project(proj)
        self.goto("roi")
        detail = ("・IMG001：pERK（Target）\n・IMG002：Actin（ローディングコントロール）\n・3群×各4生物学的反復\n\n"
                  if kind == "3groups" else
                  "・IMG001：pERK（短い露光、定量に使う）\n・IMG002：Actin（ローディングコントロール）\n"
                  "・IMG003：pERK の長い露光（飽和の例。定量には使わない設定）\n\n")
        QMessageBox.information(
            self, "教材用サンプル",
            "教材用合成データ（実験由来ではありません）でプロジェクトを作りました。\n\n" + detail +
            "ROI は設定済みです。ROI を動かして値の変化を見たり、QC・正規化・統計・Figure を順に確認してください。\n"
            "保存する場合は「名前を付けて保存」を使います。")

    def export_results(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "結果の出力先フォルダー", self.settings.value("last_dir", ""))
        if not folder:
            return
        ctrl = self.ctrl
        data = {im.image_id: d for im in ctrl.project.images if (d := ctrl.image_data(im.image_id)) is not None}
        res = ctrl.normalization()
        try:
            files = export_all(ctrl.project, ctrl.measurements(), res.sample_table, res.bio_table, data, folder)
            from matplotlib.figure import Figure
            from ..core.figure import figure_caption, plot_groups, save_figure
            from ..core.stats_runner import run_stats, stats_table_csv
            out = run_stats(ctrl.project.settings, res)
            if out.ok:
                p = Path(folder) / "stats_results.csv"
                stats_table_csv(out, p)
                files.append(str(p))
            if len(res.bio_table) and not res.blocking:
                st = self.pages["figure"][1].style()
                ann = out.annotations if (st.show_stats and out.ok) else None
                fig = plot_groups(res.bio_table, st, Figure(figsize=(st.width_in, st.height_in)), ann)
                for ext in ("png", "svg", "pdf"):
                    p = Path(folder) / f"figure.{ext}"
                    save_figure(fig, str(p), dpi=st.dpi)
                    files.append(str(p))
                s = ctrl.project.settings
                cap = figure_caption(res.bio_table, st, out.description if ann else "",
                                     f"正規化：{s.numerator_protein} ÷ {s.denominator_protein}、対照群 {s.control_group or '未設定'}。")
                p = Path(folder) / "figure_caption.txt"
                p.write_text(cap + "\n", encoding="utf-8")
                files.append(str(p))
        except Exception as e:  # noqa: BLE001
            error_box(self, "出力できません", str(e), "出力先フォルダーに書き込めるか確認してください。")
            return
        ctrl.project.log("結果を一括出力", folder)
        QMessageBox.information(self, "結果を一括出力", "次のファイルを出力しました：\n" + "\n".join(Path(f).name for f in files))

    def about(self) -> None:
        QMessageBox.about(self, "このアプリについて",
                          f"<b>Western blot 定量ツール</b> {__version__}<br>"
                          "感光フィルムのスキャン画像から、バンド定量・正規化・統計・Figure 作成までを行う研究室向けツールです。<br>"
                          "測定値は較正されていない相対的な画像強度です。ImageJ/Fiji の代替ではなく、手順の標準化と記録・教育を目的とします。")

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()
