"""プロジェクト状態・Undo/Redo・計算結果のキャッシュを管理する（GUI と core の仲介）。"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Signal

from .. import __version__
from ..core.imageio import sha256_file
from ..core.normalize import NormalizationResult, normalize
from ..core.pipeline import ImageCache, compare_with_saved, measure_project, results_to_records
from ..core.qc import QCIssue, run_qc
from ..models.project import HistoryEntry, Project, now_iso

UNDO_LIMIT = 100


class ProjectController(QObject):
    #: プロジェクト全体が差し替わった（新規・読込・Undo/Redo）
    projectReplaced = Signal()
    #: 内容が変わった（再計算が必要）
    changed = Signal()
    #: 未保存状態・Undo 可否などの表示更新
    stateChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.cache = ImageCache()
        self.project = Project(app_version=__version__)
        self.path: Path | None = None
        self.dirty = False
        self._undo: list[tuple[dict, str]] = []
        self._redo: list[tuple[dict, str]] = []
        self._state = self.project.to_dict()
        self._measure = None
        self._norm: NormalizationResult | None = None
        self._qc: list[QCIssue] | None = None
        self.load_messages: list[str] = []
        self._extremes: dict[str, tuple[float, float]] = {}   # 画像ごとの端値割合（画像は不変なのでキャッシュ）

    # ------------------------------------------------------------ 変更の確定
    def commit(self, action: str, detail: str = "", log: bool = True) -> None:
        """モデルを書き換えた後に呼ぶ。Undo 用スナップショットと履歴を残し、再計算を促す。"""
        prev = self._state
        if log:
            self.project.log(action, detail)
        self._undo.append((prev, action))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._state = self.project.to_dict()
        self.dirty = True
        self._invalidate()
        self.changed.emit()
        self.stateChanged.emit()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][1] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][1] if self._redo else ""

    def undo(self) -> None:
        if not self._undo:
            return
        state, action = self._undo.pop()
        self._redo.append((self._state, action))
        self._restore(state, f"元に戻す：{action}")

    def redo(self) -> None:
        if not self._redo:
            return
        state, action = self._redo.pop()
        self._undo.append((self._state, action))
        self._restore(state, f"やり直し：{action}")

    def _restore(self, state: dict, log_action: str) -> None:
        history = list(self.project.history)   # 履歴は Undo で消さず、Undo したこと自体を記録する
        self.project = Project.from_dict(copy.deepcopy(state))
        self.project.history = history + [HistoryEntry(now_iso(), log_action, "")]
        self._state = self.project.to_dict()
        self.dirty = True
        self._invalidate()
        self.projectReplaced.emit()
        self.changed.emit()
        self.stateChanged.emit()

    def _invalidate(self) -> None:
        self._measure = None
        self._norm = None
        self._qc = None

    # ------------------------------------------------------------ 新規・保存・読込
    def new_project(self, project: Project | None = None) -> None:
        self.project = project or Project(app_version=__version__)
        self.project.app_version = __version__
        self.path = None
        self.cache.clear()
        self._undo.clear()
        self._redo.clear()
        self._state = self.project.to_dict()
        self.dirty = project is not None
        self.load_messages = []
        self._invalidate()
        self.projectReplaced.emit()
        self.changed.emit()
        self.stateChanged.emit()

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else self.path
        if path is None:
            raise ValueError("保存先が指定されていません。")
        self.project.app_version = __version__
        self.project.last_results = results_to_records(self.measurements())
        self.project.save(path)
        self.path = path
        self.dirty = False
        self._state = self.project.to_dict()
        self.stateChanged.emit()
        return path

    def load(self, path: str | Path) -> list[str]:
        proj = Project.load(path)
        self.new_project(proj)
        self.path = Path(path)
        self.dirty = False
        self.load_messages = self.verify_images()
        if not any("見つかりません" in m for m in self.load_messages):
            diffs = compare_with_saved(self.measurements(), proj.last_results)
            if diffs:
                self.load_messages.append("保存時の測定値と再計算値が一致しません：\n  " + "\n  ".join(diffs[:10]))
            elif proj.last_results:
                self.load_messages.append(f"保存時の測定値（{len(proj.last_results)} バンド）と再計算値が一致しました。")
        self.stateChanged.emit()
        return self.load_messages

    def verify_images(self) -> list[str]:
        """全画像の存在とハッシュを確認する。"""
        msgs = []
        for im in self.project.images:
            p = Path(im.path)
            if not p.exists():
                msgs.append(f"{im.image_id}：画像ファイルが見つかりません（{im.path}）。［画像・試料情報］で再リンクしてください。")
                continue
            h = sha256_file(p)
            if h != im.sha256:
                msgs.append(
                    f"{im.image_id}：ファイルの内容（SHA-256）が登録時と異なります。原画像が変更された可能性があります。"
                    "同じ画像か確認し、意図しない変更なら元のファイルに戻してください。"
                )
        return msgs

    # ------------------------------------------------------------ 計算結果
    def image_data(self, image_id: str) -> np.ndarray | None:
        im = self.project.image(image_id)
        if im is None:
            return None
        try:
            return self.cache.get(im.path).data
        except Exception:  # noqa: BLE001
            return None

    def measurements(self) -> pd.DataFrame:
        if self._measure is None:
            self._measure = measure_project(self.project, self.cache)
        return self._measure.table

    def load_errors(self) -> dict[str, str]:
        self.measurements()
        return self._measure.errors

    def normalization(self) -> NormalizationResult:
        if self._norm is None:
            s = self.project.settings
            self._norm = normalize(self.measurements(), s.numerator_protein, s.denominator_protein,
                                   s.control_group, self.project.samples)
        return self._norm

    def qc_issues(self) -> list[QCIssue]:
        if self._qc is None:
            data = {}
            errors = dict(self.load_errors())
            for im in self.project.images:
                d = self.image_data(im.image_id)
                if d is not None:
                    data[im.image_id] = d
                elif im.image_id not in errors:
                    errors[im.image_id] = "画像を読み込めません。"
            self._qc = run_qc(self.project, self.measurements(), data, errors, self._extremes)
        return self._qc
