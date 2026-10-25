"""起動エントリーポイント：python -m wbtool"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if len(argv) > 1 and argv[1] in ("-h", "--help", "-V", "--version"):
        from .. import __version__
        print(f"wbtool {__version__}\n使い方: python -m wbtool [プロジェクト.wbproj.json]")
        return 0
    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication

    # 画像配列は (行, 列) = (y, x) の順で扱う。ROI 座標と原画像の画素が 1:1 で対応する
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Western blot 定量ツール")
    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    if len(argv) > 1 and argv[1].endswith(".json"):
        win.load_path(argv[1])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
