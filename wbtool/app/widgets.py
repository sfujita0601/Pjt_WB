"""共通ウィジェット。"""
from __future__ import annotations

import math

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableView, QVBoxLayout, QWidget,
)


def fmt(v, digits: int = 4) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if isinstance(v, float):
        if math.isnan(v):
            return "—"
        if v != 0 and (abs(v) >= 1e6 or abs(v) < 1e-3):
            return f"{v:.{digits}g}"
        return f"{v:,.{digits}g}" if abs(v) >= 1e4 else f"{v:.{digits}g}"
    return str(v)


class DataFrameModel(QAbstractTableModel):
    """pandas.DataFrame を読み取り専用で表示するモデル。"""

    def __init__(self, df: pd.DataFrame | None = None, headers: dict[str, str] | None = None):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()
        self._headers = headers or {}
        self.highlight = None   # (row) -> QColor | None

    def set_frame(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        self.endResetModel()

    def frame(self) -> pd.DataFrame:
        return self._df

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._df.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self._df.iat[index.row(), index.column()]
        if role == Qt.DisplayRole:
            return fmt(v.item() if hasattr(v, "item") else v)
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.BackgroundRole and self.highlight is not None:
            return self.highlight(index.row())
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            col = str(self._df.columns[section])
            return self._headers.get(col, col)
        return str(section + 1)


def make_table_view(model: QAbstractTableModel) -> QTableView:
    v = QTableView()
    v.setModel(model)
    v.setAlternatingRowColors(True)
    v.setSelectionBehavior(QTableView.SelectRows)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    v.horizontalHeader().setStretchLastSection(True)
    v.verticalHeader().setDefaultSectionSize(22)
    return v


class HintBox(QFrame):
    """学習モードでのみ表示する短い解説。lesson を指定すると「教材を開く」ボタンが出る。"""

    openLesson = Signal(int)

    def __init__(self, text: str, lesson: int | None = None):
        super().__init__()
        self.setObjectName("HintBox")
        self.setStyleSheet("#HintBox { background: #FFF8DC; border: 1px solid #E0C060; border-radius: 4px; }"
                           "#HintBox QLabel { color: #3a3000; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        icon = QLabel("📘 学習モード")
        icon.setStyleSheet("font-weight: bold;")
        icon.setAlignment(Qt.AlignTop)
        lay.addWidget(icon)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        lay.addWidget(self.label, 1)
        if lesson is not None:
            b = QPushButton(f"教材 {lesson:02d} を開く")
            b.clicked.connect(lambda: self.openLesson.emit(lesson))
            lay.addWidget(b, 0, Qt.AlignTop)


def page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    t = QLabel(title)
    t.setStyleSheet("font-size: 17px; font-weight: bold;")
    lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setStyleSheet("color: #555;")
        lay.addWidget(s)
    return w


def error_box(parent, title: str, why: str, how: str) -> None:
    """「なぜ失敗したか」「どう直すか」を必ず示すエラー表示。"""
    QMessageBox.warning(parent, title, f"<b>何が起きたか／なぜ</b><br>{why}<br><br><b>どう直すか</b><br>{how}")


def level_color(level: str) -> QColor | None:
    return {"エラー": QColor("#F8D0D0"), "警告": QColor("#FFF0C0"), "注意": QColor("#E8F0FF")}.get(level)
