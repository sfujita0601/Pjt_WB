"""サイドバー下の進捗表示（各段階の状態と、解析できない理由）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ..core.progress import BLOCKED, DONE, ICONS, TODO, WARN, compute_progress
from ..core.stats_runner import run_stats

COLORS = {DONE: "#1A7F37", WARN: "#9A6700", TODO: "#555555", BLOCKED: "#B00000"}


class ProgressPanel(QWidget):
    goto = Signal(str)

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        t = QLabel("<b>進捗</b>（クリックで移動）")
        lay.addWidget(t)
        self.lst = QListWidget()
        self.lst.setStyleSheet("QListWidget { font-size: 12px; border: 1px solid #DDD; } QListWidget::item { padding: 2px; }")
        self.lst.setWordWrap(True)
        self.lst.itemClicked.connect(lambda it: self.goto.emit(it.data(Qt.UserRole)))
        lay.addWidget(self.lst)
        self.lbl_next = QLabel()
        self.lbl_next.setWordWrap(True)
        self.lbl_next.setStyleSheet("font-size: 11px; color:#333;")
        lay.addWidget(self.lbl_next)
        ctrl.changed.connect(self.refresh)

    def refresh(self) -> None:
        c = self.ctrl
        norm = c.normalization()
        out = run_stats(c.project.settings, norm)
        steps = compute_progress(c.project, c.measurements(), norm, c.qc_issues(), out.ok, out.refusal)
        self.lst.clear()
        for s in steps:
            it = QListWidgetItem(f"{ICONS[s.status]} {s.title}")
            it.setData(Qt.UserRole, s.key)
            it.setToolTip("\n".join(s.details))
            it.setForeground(QColor(COLORS[s.status]))
            self.lst.addItem(it)
        nxt = next((s for s in steps if s.status in (TODO, BLOCKED)), None) or next((s for s in steps if s.status == WARN), None)
        self.lbl_next.setText(f"<b>次にすること：</b>{nxt.title} — {nxt.details[0]}" if nxt else "<b>すべての段階が完了しています。</b>")
        self.steps = steps
