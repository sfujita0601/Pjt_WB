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
    w.show()   # 非表示の画面は遅延更新されるため、実際の利用と同じく表示する
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


@pytest.fixture
def win3(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    from wbtool.app.main_window import MainWindow
    from wbtool.core.examples import build_example_project
    w = MainWindow()
    w.show()
    w.ctrl.new_project(build_example_project(tmp_path / "ex3", kind="3groups"))
    app.processEvents()
    yield w
    w.ctrl.dirty = False
    w.close()


def test_stats_page_multi_group_guided(win3, app, tmp_path, monkeypatch):
    page = win3.pages["stats"][1]
    win3.goto("stats")
    app.processEvents()
    assert "Games–Howell" in page.lbl_method.text()
    page.chk_confirm.setChecked(True)
    txt = page.txt.toPlainText()
    assert "Welch の一元配置 ANOVA" in txt and "調整済み p" in txt
    page.chk_eqvar.setChecked(True)          # 等分散を仮定 → Dunnett
    app.processEvents()
    assert "Dunnett" in page.txt.toPlainText()
    for b in page.grp_design.buttons():       # 対応あり3群 → 検定しない
        if b.property("key") == "paired":
            b.setChecked(True)
    app.processEvents()
    assert "検定しませんでした" in page.txt.toPlainText()
    win3.ctrl.undo()
    app.processEvents()
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "st.csv"), ""))
    page.chk_confirm.setChecked(False)
    page.chk_confirm.setChecked(True)
    page._export()
    import pandas as pd
    t = pd.read_csv(tmp_path / "st.csv")
    assert {"p_adj", "diff", "ci_low", "method"} <= set(t.columns) and len(t) == 2


def test_figure_with_stats_and_band_panel(win3, app, tmp_path, monkeypatch):
    page = win3.pages["figure"][1]
    win3.goto("figure")
    app.processEvents()
    page.chk_stats.setChecked(True)
    page.cmb_err.setCurrentIndex(page.cmb_err.findData("sem"))
    page._on_change()
    app.processEvents()
    cap = page.txt_caption.toPlainText()
    assert "SEM" in cap and "調整済み" in cap and "生物学的反復" in cap
    page.tabs.setCurrentIndex(1)
    app.processEvents()
    assert len(page._panel_rows) == 2
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "comp.svg"), ""))
    page._save("svg")
    import json
    rec = json.loads((tmp_path / "comp_bands.json").read_text(encoding="utf-8"))
    assert len(rec["rows"]) == 2 and "sha256" in rec["rows"][0] and "crop" in rec["rows"][0]
    assert (tmp_path / "comp_crop_IMG001.tif").exists()


def test_validation_dialog_flow(win3, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from wbtool.app.validation_dialog import ValidationDialog
    from tests.test_validation import fake_fiji_csv
    dlg = ValidationDialog(win3.ctrl)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "m.ijm"), ""))
    dlg._save_macro()
    assert "makeRectangle" in (tmp_path / "m.ijm").read_text(encoding="utf-8")
    p = win3.ctrl.project
    fake_fiji_csv(p, "IMG001", win3.ctrl.image_data("IMG001"), tmp_path / "f.csv")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(tmp_path / "f.csv"), ""))
    dlg._load_csv()
    assert dlg.reports["IMG001"].all_match
    assert "一致しました" in dlg.lbl.text()


def test_progress_panel_and_roles(win3, app):
    panel = win3.progress
    panel.refresh()
    titles = [panel.lst.item(i).text() for i in range(panel.lst.count())]
    assert any("正規化" in t for t in titles)
    page = win3.pages["norm"][1]
    win3.goto("norm")
    app.processEvents()
    assert page.tbl_roles.rowCount() == 2
    win3.ctrl.project.samples[0].bio_rep = ""
    win3.ctrl.commit("test")
    panel.refresh()
    assert "S1" in panel.lbl_next.text() or "試料情報" in panel.lbl_next.text()


def test_roi_filter_unset_protein(win, app):
    page = win.pages["roi"][1]
    im = win.ctrl.project.image("IMG001")
    im.protein_target = ""
    win.ctrl.commit("test")
    app.processEvents()
    idx = next(i for i in range(page.cmb_filter.count()) if "未設定" in page.cmb_filter.itemText(i))
    page.cmb_filter.setCurrentIndex(idx)
    app.processEvents()
    assert page.tbl_bands.rowCount() == len(im.bands)


def test_figure_save_refused_when_blocking(win, app, tmp_path, monkeypatch):
    page = win.pages["figure"][1]
    win.goto("figure")
    res = win.ctrl.normalization()
    res.blocking.append("テスト用の停止理由")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok)
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "x.png"), ""))
    page._save("png")
    assert warned and not (tmp_path / "x.png").exists()
    assert len(page.current_figure().axes[0].collections) == 0
