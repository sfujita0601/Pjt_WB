"""大きな画像での応答性の計測（仮の試験条件。実データの代表値ではない）。

    .venv/bin/python -u benchmarks/bench_large_image.py [幅 高さ 画像枚数]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import tifffile  # noqa: E402


def timeit(label, fn, repeat=5):
    ts = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    print(f"{label:<48s} 中央値 {np.median(ts) * 1000:8.1f} ms（{repeat} 回）")
    return np.median(ts)


def main(w=6000, h=4000, n_images=1, n_lanes=12):
    import pyqtgraph as pg
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)
    from PySide6.QtWidgets import QApplication
    from wbtool.app.main_window import MainWindow
    from wbtool.core.imageio import load_image, sha256_file
    from wbtool.core.synthetic import make_blot
    from wbtool.models.project import BandROI, ImageRecord, Lane, Project, Rect, Sample, now_iso

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp())
    lane_w = w // (n_lanes + 2)
    blot = make_blot([8000 + 1000 * i for i in range(n_lanes)], width=w, height=h, lane_width=lane_w,
                     lane_gap=lane_w // 6, band_height=h // 40, background=50000, noise_sd=300, dtype=np.uint16)
    path = tmp / "large.tif"
    tifffile.imwrite(path, blot.data)
    print(f"画像 {w}×{h} 16-bit、ファイル {path.stat().st_size / 1e6:.0f} MB、レーン {n_lanes}")
    timeit("画像の読込（tifffile）", lambda: load_image(path), 3)
    timeit("SHA-256", lambda: sha256_file(path), 3)

    p = Project()
    li = load_image(path)
    im = ImageRecord("IMG001", str(path), sha256_file(path), w, h, 16, "uint16", "TIFF", now_iso(), protein_target="T")
    for i, (lr, br) in enumerate(zip(blot.lanes, blot.bands)):
        im.lanes.append(Lane(f"L{i + 1}", lr, f"S{i + 1}"))
        im.bands.append(BandROI(f"B{i + 1}", f"L{i + 1}", Rect(br.x, br.y - 5, br.w, br.h + 10)))
        p.samples.append(Sample(f"S{i + 1}", "C" if i % 2 else "T", f"R{i}"))
    p.images.append(im)
    import copy
    for k in range(2, n_images + 1):   # 同じ内容の別ファイルを追加（多数枚の負荷を見るため）
        pk = tmp / f"large_{k}.tif"
        tifffile.imwrite(pk, blot.data)
        imk = copy.deepcopy(im)
        imk.image_id, imk.path = f"IMG{k:03d}", str(pk)
        p.images.append(imk)
    print(f"登録画像 {len(p.images)} 枚")

    win = MainWindow()
    win.resize(1500, 950)
    win.show()
    win.ctrl.new_project(p)
    for imx in p.images:
        win.ctrl.cache.put(imx.path, li)
    page = win.pages["roi"][1]
    win.open_roi("IMG001")
    app.processEvents()
    roi = page._items[("band", "B1")]
    page._select(("band", "B1"))

    def drag_step():
        roi.setPos(roi.pos() + pg.Point(1, 0), finish=False)
        app.processEvents()
    timeit("ROI ドラッグ中の1ステップ（測定・プロファイル更新）", drag_step, 20)

    def drag_finish():
        roi.setPos(roi.pos() + pg.Point(1, 0), finish=True)
        app.processEvents()
    timeit("ROI ドラッグ終了（確定・表示中の画面の再計算）", drag_finish, 5)

    def qc_page():
        win.goto("qc")
        app.processEvents()
        win.goto("roi")
        app.processEvents()
    timeit("確定後に QC 画面を開く（全画像のQC）", lambda: (drag_finish(), qc_page()), 3)

    def display_toggle():
        page.cmb_display.setCurrentIndex((page.cmb_display.currentIndex() + 1) % page.cmb_display.count())
        app.processEvents()
    timeit("表示モードの切替", display_toggle, 4)

    def zoom():
        page.vb.scaleBy((0.9, 0.9))
        app.processEvents()
    timeit("ズーム1段", zoom, 10)
    win.ctrl.dirty = False
    os._exit(0)


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:4]]
    main(*args)
