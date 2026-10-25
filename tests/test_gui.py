"""GUI の結合テスト（画面を表示せずに実行）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pg = pytest.importorskip("pyqtgraph")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    from wbtool.app.main_window import MainWindow
    from wbtool.core.examples import build_example_project
    w = MainWindow()
    w.ctrl.new_project(build_example_project(tmp_path / "ex"))
    w.open_roi("IMG001")
    app.processEvents()
    yield w
    w.ctrl.dirty = False
    w.close()


def corrected(w, iid, bid):
    m = w.ctrl.measurements()
    return float(m[(m.image_id == iid) & (m.band_id == bid)]["corrected"].iloc[0])


def test_roi_drag_updates_live_and_commits_and_undo(win, app):
    page = win.pages["roi"][1]
    before = corrected(win, "IMG001", "B1")
    roi = page._items[("band", "B1")]
    page._select(("band", "B1"))
    pos = roi.pos()
    roi.setPos(pos + pg.Point(0, 20), finish=False)          # ドラッグ中
    app.processEvents()
    assert "ドラッグ中" in page.lbl_formula.text()
    assert win.ctrl.project.image("IMG001").band("B1").rect.y == round(pos.y())   # まだ確定していない
    roi.setPos(roi.pos(), finish=True)                       # ドラッグ終了 → 確定
    app.processEvents()
    assert win.ctrl.project.image("IMG001").band("B1").rect.y == round(pos.y()) + 20
    assert corrected(win, "IMG001", "B1") != pytest.approx(before)
    assert win.ctrl.project.history[-1].action == "バンドROI変更"
    win.ctrl.undo()
    app.processEvents()
    assert corrected(win, "IMG001", "B1") == pytest.approx(before)
    win.ctrl.redo()
    assert corrected(win, "IMG001", "B1") != pytest.approx(before)


def test_display_invert_does_not_change_values(win, app):
    page = win.pages["roi"][1]
    before = win.ctrl.measurements()["corrected"].tolist()
    for i in range(page.cmb_display.count()):
        page.cmb_display.setCurrentIndex(i)
        app.processEvents()
    win.ctrl._invalidate()
    assert win.ctrl.measurements()["corrected"].tolist() == before


def test_polarity_and_bg_method_change_values_and_history(win, app):
    page = win.pages["roi"][1]
    before = corrected(win, "IMG001", "B1")
    page.cmb_polarity.setCurrentIndex(1)
    page._on_polarity(1)
    assert corrected(win, "IMG001", "B1") == pytest.approx(-before)
    win.ctrl.undo()
    page.refresh()
    page.cmb_bg.setCurrentIndex(2)       # 背景補正なし
    page._on_bg_method(2)
    assert corrected(win, "IMG001", "B1") > before
    assert win.ctrl.project.history[-1].action == "背景推定法の変更"


def test_unify_size(win, app):
    page = win.pages["roi"][1]
    im = win.ctrl.project.image("IMG001")
    im.bands[2].rect = type(im.bands[2].rect)(im.bands[2].rect.x, im.bands[2].rect.y, 10, 30)
    win.ctrl.commit("test")
    page._select(("band", "B1"))
    page._unify_size()
    sizes = {(b.rect.w, b.rect.h) for b in win.ctrl.project.image("IMG001").bands}
    assert len(sizes) == 1


def test_save_load_roundtrip_via_controller(win, app, tmp_path):
    from wbtool.core.imageio import sha256_file
    hashes = [sha256_file(im.path) for im in win.ctrl.project.images]
    before = win.ctrl.measurements()[["band_id", "corrected"]].values.tolist()
    path = tmp_path / "p.wbproj.json"
    win.ctrl.save(path)
    msgs = win.ctrl.load(path)
    assert any("一致しました" in m for m in msgs)
    assert win.ctrl.measurements()[["band_id", "corrected"]].values.tolist() == before
    assert [sha256_file(im.path) for im in win.ctrl.project.images] == hashes


def test_qc_exclude_decision_removes_band_from_normalization(win, app):
    page = win.pages["qc"][1]
    page.refresh()
    win.ctrl.project.image("IMG003").include = True     # 過露光画像も「使う」→ 露光の重複警告
    win.ctrl.commit("test")
    page.refresh()
    assert any(q.category == "露光の重複" for q in page._issues)
    sat = [q for q in page._issues if q.category == "飽和候補（バンド内）"]
    assert sat
    page._on_decision(sat[0].key, "除外")
    im = win.ctrl.project.image(sat[0].image_id)
    assert im.band(sat[0].band_ids[0]).excluded


def test_stats_page_requires_confirmation(win, app):
    page = win.pages["stats"][1]
    win.goto("stats")
    page.refresh()
    assert "確認してチェック" in page.txt.toPlainText()
    page.chk_confirm.setChecked(True)
    txt = page.txt.toPlainText()
    assert "Welch" in txt and "p =" in txt and "Hedges" in txt


def test_figure_page_renders_and_saves(win, app, tmp_path, monkeypatch):
    page = win.pages["figure"][1]
    page.refresh()
    from PySide6.QtWidgets import QFileDialog
    for ext in ("png", "svg", "pdf"):
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, e=ext, **k: (str(tmp_path / f"f.{e}"), ""))
        page._save(ext)
        assert (tmp_path / f"f.{ext}").stat().st_size > 0


def test_learning_pages_load(win, app):
    from wbtool.app.learn_page import DOCS, DOCS_DIR, LESSON_DIR, LESSONS
    for f, _t in LESSONS:
        text = (LESSON_DIR / f).read_text(encoding="utf-8")
        for h in ("(1) 何を理解するか", "(4) 数式・原理", "(7) 解答・解説"):
            assert h in text, (f, h)
    for f, _t in DOCS:
        assert (DOCS_DIR / f).exists(), f
    win.open_lesson(3)
    assert "背景" in win.pages["learn"][1].browser.toPlainText()
