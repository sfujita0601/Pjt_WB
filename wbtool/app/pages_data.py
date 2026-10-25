"""［プロジェクト］［画像・試料情報］画面。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ..core.export import samples_from_csv, samples_to_csv
from ..core.imageio import SUPPORTED_SUFFIXES, ImageLoadError, load_image, sha256_file
from ..models.project import POLARITY_BRIGHT, POLARITY_DARK, ImageRecord, Sample, now_iso
from .widgets import HintBox, LazyRefreshMixin, error_box, page_header


class ProjectPage(LazyRefreshMixin, QWidget):
    requestNew = Signal()
    requestOpen = Signal()
    requestSave = Signal()
    requestSaveAs = Signal()
    requestExample = Signal()
    requestExport = Signal()

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("プロジェクト", "1つの実験（または一連の解析）を1つのプロジェクトファイル（.wbproj.json）に保存します。"
                                  "原画像はコピー・変更せず、ファイルの場所と SHA-256 ハッシュで参照します。"))
        self.hint = HintBox(
            "はじめての人は<b>「教材用サンプルを開く」</b>から始めてください（教材用合成データ。実験データではありません）。"
            "解析の流れ：画像・試料情報 → レーン・バンドROI → 背景補正・定量 → QC → 正規化 → 統計 → Figure。"
            "どの画面でも <b>⌘Z / Ctrl+Z</b> で元に戻せます。", lesson=7)
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        for text, sig in (("新規プロジェクト", self.requestNew), ("開く…", self.requestOpen), ("保存", self.requestSave),
                          ("名前を付けて保存…", self.requestSaveAs), ("教材用サンプルを開く", self.requestExample),
                          ("結果を一括出力…", self.requestExport)):
            b = QPushButton(text)
            b.clicked.connect(sig.emit)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        g = QGroupBox("プロジェクト情報")
        f = QFormLayout(g)
        self.ed_name = QLineEdit()
        self.ed_name.editingFinished.connect(self._on_name)
        f.addRow("名前", self.ed_name)
        self.lbl_path = QLabel()
        self.lbl_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow("保存先", self.lbl_path)
        self.lbl_info = QLabel()
        f.addRow("内容", self.lbl_info)
        self.lbl_dates = QLabel()
        f.addRow("作成／更新", self.lbl_dates)
        lay.addWidget(g)

        g2 = QGroupBox("読込時の確認結果（画像の存在・ハッシュ・測定値の再現）")
        v = QVBoxLayout(g2)
        self.txt_msgs = QPlainTextEdit()
        self.txt_msgs.setReadOnly(True)
        v.addWidget(self.txt_msgs)
        lay.addWidget(g2, 1)

        g3 = QGroupBox("操作履歴（新しい順。ROI の変更・設定変更・除外などがすべて記録されます）")
        v = QVBoxLayout(g3)
        self.tbl_hist = QTableWidget()
        self.tbl_hist.setColumnCount(3)
        self.tbl_hist.setHorizontalHeaderLabels(["日時", "操作", "詳細"])
        self.tbl_hist.horizontalHeader().setStretchLastSection(True)
        self.tbl_hist.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_hist.verticalHeader().setVisible(False)
        v.addWidget(self.tbl_hist)
        lay.addWidget(g3, 2)
        ctrl.changed.connect(self.request_refresh)
        ctrl.stateChanged.connect(self.refresh)

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    def refresh(self) -> None:
        p = self.ctrl.project
        if self.ed_name.text() != p.name:
            self.ed_name.setText(p.name)
        self.lbl_path.setText(str(self.ctrl.path) if self.ctrl.path else "（未保存）")
        n_b = sum(len(im.bands) for im in p.images)
        self.lbl_info.setText(f"画像 {len(p.images)} 枚、試料 {len(p.samples)} 件、バンドROI {n_b} 個"
                              + ("　● 未保存の変更があります" if self.ctrl.dirty else ""))
        self.lbl_dates.setText(f"{p.created_at} ／ {p.modified_at}")
        self.txt_msgs.setPlainText("\n".join(self.ctrl.load_messages) or "（なし）")
        hist = list(reversed(p.history))[:500]
        self.tbl_hist.setRowCount(len(hist))
        for r, h in enumerate(hist):
            for c, v in enumerate((h.timestamp, h.action, h.detail)):
                self.tbl_hist.setItem(r, c, QTableWidgetItem(v))
        self.tbl_hist.resizeColumnToContents(0)
        self.tbl_hist.resizeColumnToContents(1)

    def _on_name(self) -> None:
        name = self.ed_name.text().strip()
        if name and name != self.ctrl.project.name:
            old = self.ctrl.project.name
            self.ctrl.project.name = name
            self.ctrl.commit("プロジェクト名の変更", f"{old} → {name}")


IMG_COLS = ["image_id", "状態", "タンパク質", "定量に使う", "極性", "experiment_id", "blot_id", "membrane_id",
            "exposure_id", "メモ", "ファイル", "サイズ", "bit", "形式", "SHA-256（先頭12桁）", "登録日時"]
EDITABLE_TEXT = {2: "protein_target", 5: "experiment_id", 6: "blot_id", 7: "membrane_id", 8: "exposure_id", 9: "notes"}
SAMPLE_COLS = ["sample_id", "group", "bio_rep", "tech_rep", "notes", "使用レーン"]


class ImagesPage(LazyRefreshMixin, QWidget):
    openRoi = Signal(str)

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        self._status: dict[str, str] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("画像・試料情報", "画像を登録し、タンパク質名・膜・露光などの情報と、試料（sample_id・群・反復）を入力します。"))
        self.hint = HintBox(
            "Target と Loading control が別の画像でも、<b>同じ sample_id</b> をレーンに割り当てれば対応づけられます。"
            "<b>biological replicate（bio_rep）</b>は独立な個体・培養など統計の n になる単位、"
            "<b>technical replicate（tech_rep）</b>は同じ試料の繰り返しロードで、n には数えません。"
            "同じ膜の別露光は独立な反復ではないので、定量には1枚だけ使います（「定量に使う」）。", lesson=5)
        lay.addWidget(self.hint)
        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for text, fn in (("画像を追加…", self._add_images), ("選択画像のROIを編集", self._open_roi),
                         ("再リンク…", self._relink), ("ハッシュを再確認", self._verify), ("登録を削除", self._remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        tl.addLayout(row)
        self.tbl = QTableWidget()
        self.tbl.setColumnCount(len(IMG_COLS))
        self.tbl.setHorizontalHeaderLabels(IMG_COLS)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.itemChanged.connect(self._on_img_item)
        self.tbl.doubleClicked.connect(lambda idx: self._open_roi() if idx.column() in (0, 1, 10) else None)
        tl.addWidget(self.tbl)
        note = QLabel("TIFF / PNG（8・16-bit）を推奨。JPEG は不可逆圧縮のため警告付きで扱います。原画像ファイルは変更しません。")
        note.setStyleSheet("color:#555;")
        tl.addWidget(note)
        split.addWidget(top)

        bot = QWidget()
        bl = QVBoxLayout(bot)
        bl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>試料表</b>"))
        for text, fn in (("行を追加", self._add_sample), ("選択行を削除", self._del_sample),
                         ("CSV から取込…", self._import_csv), ("CSV へ出力…", self._export_csv)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        self.tbl_s = QTableWidget()
        self.tbl_s.setColumnCount(len(SAMPLE_COLS))
        self.tbl_s.setHorizontalHeaderLabels(SAMPLE_COLS)
        self.tbl_s.horizontalHeader().setStretchLastSection(True)
        self.tbl_s.verticalHeader().setVisible(False)
        self.tbl_s.itemChanged.connect(self._on_sample_item)
        bl.addWidget(self.tbl_s)
        note = QLabel("CSV の列：sample_id, group, bio_rep, tech_rep, notes［, image_id, lane_id］"
                      "（image_id と lane_id があれば、そのレーンに sample_id を割り当てます）")
        note.setStyleSheet("color:#555;")
        bl.addWidget(note)
        split.addWidget(bot)
        ctrl.changed.connect(self.request_refresh)
        ctrl.projectReplaced.connect(lambda: self._status.clear())

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    # ---------------------------------------------------------------- 表示
    def refresh(self) -> None:
        self._building = True
        try:
            self._fill_images()
            self._fill_samples()
        finally:
            self._building = False

    def _fill_images(self) -> None:
        p = self.ctrl.project
        t = self.tbl
        t.setRowCount(len(p.images))
        for r, im in enumerate(p.images):
            status = self._status.get(im.image_id) or ("OK" if Path(im.path).exists() else "ファイルなし")
            vals = [im.image_id, status, im.protein_target, None, None, im.experiment_id, im.blot_id, im.membrane_id,
                    im.exposure_id, im.notes, im.path, f"{im.width}×{im.height}", str(im.bit_depth), im.file_format,
                    im.sha256[:12], im.loaded_at]
            for c, v in enumerate(vals):
                if c in (3, 4):
                    continue
                it = QTableWidgetItem(v)
                if c not in EDITABLE_TEXT:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c == 1 and status != "OK":
                    it.setForeground(Qt.red)
                if c == 10:
                    it.setToolTip(im.path)
                t.setItem(r, c, it)
            chk = QTableWidgetItem()
            chk.setFlags((chk.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            chk.setCheckState(Qt.Checked if im.include else Qt.Unchecked)
            chk.setToolTip("同じ膜の別露光が複数ある場合、飽和していない1枚だけにチェックを入れます。")
            t.setItem(r, 3, chk)
            cmb = QComboBox()
            cmb.addItem("暗いバンド", POLARITY_DARK)
            cmb.addItem("明るいバンド", POLARITY_BRIGHT)
            cmb.setCurrentIndex(0 if im.polarity == POLARITY_DARK else 1)
            cmb.activated.connect(lambda _i, c=cmb, iid=im.image_id: self._set_polarity(iid, c.currentData()))
            t.setCellWidget(r, 4, cmb)
        t.resizeColumnsToContents()

    def _fill_samples(self) -> None:
        p = self.ctrl.project
        t = self.tbl_s
        t.setRowCount(len(p.samples))
        usage: dict[str, list[str]] = {}
        for im in p.images:
            for l in im.lanes:
                if l.sample_id:
                    usage.setdefault(l.sample_id, []).append(f"{im.image_id}/{l.lane_id}")
        for r, s in enumerate(p.samples):
            for c, v in enumerate((s.sample_id, s.group, s.bio_rep, s.tech_rep, s.notes, ", ".join(usage.get(s.sample_id, [])))):
                it = QTableWidgetItem(v)
                if c == 5:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()

    # ---------------------------------------------------------------- 画像
    def _selected_image_id(self) -> str:
        rows = self.tbl.selectionModel().selectedRows()
        if not rows:
            return ""
        return self.tbl.item(rows[0].row(), 0).text()

    def _add_images(self) -> None:
        pattern = "画像 (" + " ".join(f"*{s}" for s in SUPPORTED_SUFFIXES) + ")"
        paths, _ = QFileDialog.getOpenFileNames(self, "画像を追加", "", pattern)
        if not paths:
            return
        p = self.ctrl.project
        added, warns = [], []
        for path in paths:
            path = str(Path(path).resolve())
            if any(Path(im.path) == Path(path) for im in p.images):
                warns.append(f"{Path(path).name}：すでに登録されています。")
                continue
            try:
                li = load_image(path)
            except ImageLoadError as e:
                warns.append(str(e))
                continue
            self.ctrl.cache.put(path, li)
            im = ImageRecord(p.next_image_id(), path, sha256_file(path), li.data.shape[1], li.data.shape[0],
                             li.bit_depth, li.dtype, li.file_format, now_iso(), color_converted=li.color_converted)
            p.images.append(im)
            added.append(f"{im.image_id}={Path(path).name}（{li.bit_depth}-bit, sha256 {im.sha256[:12]}…）")
            warns.extend(f"{Path(path).name}：{w}" for w in li.warnings)
        if added:
            self.ctrl.commit("画像を追加", "; ".join(added))
        if warns:
            QMessageBox.warning(self, "画像の追加", "\n\n".join(warns))
        if added:
            QMessageBox.information(self, "画像の追加", f"{len(added)} 枚を登録しました。\n次に表で「タンパク質」「membrane_id」「exposure_id」を入力してください。")

    def _open_roi(self) -> None:
        iid = self._selected_image_id()
        if iid:
            self.openRoi.emit(iid)

    def _relink(self) -> None:
        iid = self._selected_image_id()
        im = self.ctrl.project.image(iid)
        if im is None:
            QMessageBox.information(self, "再リンク", "表で画像を1つ選択してください。")
            return
        path, _ = QFileDialog.getOpenFileName(self, f"{iid} の新しい場所", str(Path(im.path).parent),
                                              "画像 (" + " ".join(f"*{s}" for s in SUPPORTED_SUFFIXES) + ")")
        if not path:
            return
        h = sha256_file(path)
        if h != im.sha256:
            if QMessageBox.warning(
                self, "再リンク", "選んだファイルの内容（SHA-256）が登録時と異なります。\n"
                "別の画像か、元の画像が編集された可能性があります。ROI がずれたり測定値が変わったりします。\n\n"
                "それでもこのファイルにリンクしますか？", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        try:
            li = load_image(path)
        except ImageLoadError as e:
            error_box(self, "再リンク", str(e), "別のファイルを選んでください。")
            return
        if li.data.shape != (im.height, im.width):
            error_box(self, "再リンク", f"画像サイズが異なります（登録時 {im.width}×{im.height}、選択 {li.data.shape[1]}×{li.data.shape[0]}）。",
                      "ROI 座標が意味を失うため、同じ画像ファイルを選んでください。")
            return
        old = im.path
        self.ctrl.cache.drop(old)
        im.path = str(Path(path).resolve())
        detail = f"{iid}: {old} → {im.path}"
        if h != im.sha256:
            detail += f"（ハッシュ変更 {im.sha256[:12]} → {h[:12]}）"
            im.sha256 = h
        self._status.pop(iid, None)
        self.ctrl.commit("画像の再リンク", detail)

    def _verify(self) -> None:
        msgs = self.ctrl.verify_images()
        self._status.clear()
        for im in self.ctrl.project.images:
            if not Path(im.path).exists():
                self._status[im.image_id] = "ファイルなし"
            elif any(m.startswith(im.image_id) for m in msgs):
                self._status[im.image_id] = "ハッシュ不一致"
            else:
                self._status[im.image_id] = "OK（確認済）"
        self.refresh()
        QMessageBox.information(self, "ハッシュ確認", "\n".join(msgs) if msgs else "すべての画像が登録時と同じ内容です（SHA-256 一致）。")

    def _remove(self) -> None:
        iid = self._selected_image_id()
        im = self.ctrl.project.image(iid)
        if im is None:
            return
        if QMessageBox.question(self, "登録を削除", f"{iid} の登録と、その ROI {len(im.bands)} 個をプロジェクトから削除します。\n"
                                "（画像ファイル自体は削除しません。元に戻す で復元できます）") != QMessageBox.Yes:
            return
        self.ctrl.project.images.remove(im)
        self.ctrl.commit("画像の登録を削除", f"{iid}: {im.path}")

    def _on_img_item(self, item: QTableWidgetItem) -> None:
        if self._building:
            return
        im = self.ctrl.project.image(self.tbl.item(item.row(), 0).text())
        if im is None:
            return
        c = item.column()
        if c in EDITABLE_TEXT:
            attr = EDITABLE_TEXT[c]
            new = item.text().strip()
            if getattr(im, attr) != new:
                old = getattr(im, attr)
                setattr(im, attr, new)
                QTimer.singleShot(0, lambda: self.ctrl.commit("画像情報の編集", f"{im.image_id}.{attr}: {old} → {new}"))
        elif c == 3:
            inc = item.checkState() == Qt.Checked
            if inc != im.include:
                im.include = inc
                QTimer.singleShot(0, lambda: self.ctrl.commit("定量に使う画像の変更", f"{im.image_id}: {'使う' if inc else '使わない'}"))

    def _set_polarity(self, iid: str, pol: str) -> None:
        im = self.ctrl.project.image(iid)
        if im and im.polarity != pol:
            im.polarity = pol
            self.ctrl.commit("バンド極性の変更", f"{iid}: {pol}")

    # ---------------------------------------------------------------- 試料
    def _add_sample(self) -> None:
        p = self.ctrl.project
        n = len(p.samples) + 1
        while p.sample(f"S{n}"):
            n += 1
        p.samples.append(Sample(f"S{n}"))
        self.ctrl.commit("試料を追加", f"S{n}")

    def _del_sample(self) -> None:
        rows = self.tbl_s.selectionModel().selectedRows() or (
            [self.tbl_s.currentIndex()] if self.tbl_s.currentIndex().isValid() else [])
        if not rows:
            return
        sid = self.tbl_s.item(rows[0].row(), 0).text()
        s = self.ctrl.project.sample(sid)
        used = [f"{im.image_id}/{l.lane_id}" for im in self.ctrl.project.images for l in im.lanes if l.sample_id == sid]
        if used and QMessageBox.question(self, "試料を削除", f"{sid} はレーン {', '.join(used)} で使われています。"
                                         "削除するとそれらのレーンは未割当になります。続けますか？") != QMessageBox.Yes:
            return
        self.ctrl.project.samples.remove(s)
        for im in self.ctrl.project.images:
            for l in im.lanes:
                if l.sample_id == sid:
                    l.sample_id = ""
        self.ctrl.commit("試料を削除", sid)

    def _on_sample_item(self, item: QTableWidgetItem) -> None:
        if self._building:
            return
        p = self.ctrl.project
        r, c = item.row(), item.column()
        if r >= len(p.samples) or c > 4:
            return
        s = p.samples[r]
        attr = ["sample_id", "group", "bio_rep", "tech_rep", "notes"][c]
        new = item.text().strip()
        old = getattr(s, attr)
        if new == old:
            return
        if attr == "sample_id":
            if not new or p.sample(new):
                QMessageBox.warning(self, "sample_id", "sample_id は空にできず、ほかの試料と重複できません。")
                QTimer.singleShot(0, self.refresh)
                return
            for im in p.images:      # レーン側の参照も追従させる
                for l in im.lanes:
                    if l.sample_id == old:
                        l.sample_id = new
        setattr(s, attr, new)
        QTimer.singleShot(0, lambda: self.ctrl.commit("試料情報の編集", f"{old if attr == 'sample_id' else s.sample_id}.{attr}: {old} → {new}"))

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "試料表 CSV を取込", "", "CSV (*.csv)")
        if not path:
            return
        try:
            msgs = samples_from_csv(self.ctrl.project, path)
        except Exception as e:  # noqa: BLE001
            error_box(self, "CSV 取込", str(e), "1行目が列名（sample_id, group, bio_rep, tech_rep, notes, image_id, lane_id）の"
                      "UTF-8 の CSV か確認してください。Excel では「CSV UTF-8」で保存します。")
            return
        self.ctrl.commit("試料表 CSV 取込", f"{path}: {msgs[0]}")
        QMessageBox.information(self, "CSV 取込", "\n".join(msgs))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "試料表 CSV へ出力", "samples.csv", "CSV (*.csv)")
        if path:
            samples_to_csv(self.ctrl.project, path)
