"""画像の読込。原画像の階調（8/16 bit）を保持し、ファイルは一切変更しない。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SUPPORTED_SUFFIXES = (".tif", ".tiff", ".png", ".jpg", ".jpeg")
LOSSY_SUFFIXES = (".jpg", ".jpeg")


class ImageLoadError(Exception):
    """読込失敗。message は学生向けに「なぜ・どう直すか」を含める。"""


@dataclass
class LoadedImage:
    data: np.ndarray            # 2次元、原画像の dtype のまま（uint8 / uint16 等）
    bit_depth: int
    dtype: str
    file_format: str
    color_converted: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def max_value(self) -> int:
        return dtype_max(self.data.dtype)


def dtype_max(dtype) -> int:
    dtype = np.dtype(dtype)
    if np.issubdtype(dtype, np.integer):
        return int(np.iinfo(dtype).max)
    return 1  # float 画像は 0–1 を想定（通常は使わない）


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    if not path.exists():
        raise ImageLoadError(
            f"ファイルが見つかりません：{path}\n"
            "→ ファイルを移動・改名した場合は、［画像・試料情報］で「再リンク」を使って新しい場所を指定してください。"
        )
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ImageLoadError(
            f"対応していない形式です（{suffix}）。\n→ TIFF（推奨）または PNG で保存し直してください。"
        )
    warnings: list[str] = []
    try:
        if suffix in (".tif", ".tiff"):
            import tifffile
            arr = tifffile.imread(str(path))
            fmt = "TIFF"
        else:
            from PIL import Image
            with Image.open(path) as im:
                fmt = im.format or suffix.lstrip(".").upper()
                if im.mode in ("I;16", "I;16B", "I;16L"):
                    arr = np.array(im, dtype=np.uint16)
                elif im.mode == "I":
                    arr = np.array(im)
                    if arr.min() >= 0 and arr.max() <= 65535:
                        arr = arr.astype(np.uint16)
                elif im.mode in ("L", "RGB", "RGBA", "LA"):
                    arr = np.array(im)
                elif im.mode == "P":
                    arr = np.array(im.convert("RGB"))
                else:
                    arr = np.array(im)
    except ImageLoadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ImageLoadError(
            f"画像を読み込めませんでした：{path.name}\n原因：{e}\n"
            "→ ファイルが壊れていないか、ほかのソフト（Fiji 等）で開けるか確認してください。"
        ) from e

    arr = np.asarray(arr)
    if arr.ndim == 3 and arr.shape[0] in (1,) :
        arr = arr[0]
    color_converted = False
    if arr.ndim == 3:
        if arr.shape[-1] in (2, 4):  # アルファ付き
            arr = arr[..., : arr.shape[-1] - 1]
        if arr.shape[-1] == 1:
            arr = arr[..., 0]
        elif arr.shape[-1] == 3:
            r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
            if np.array_equal(r, g) and np.array_equal(g, b):
                arr = r.copy()
                warnings.append("RGB 形式ですが3チャンネルが同一のため、1チャンネルを使用しました（値は変わりません）。")
            else:
                # 輝度（ITU-R BT.601）で変換。元の dtype の範囲に丸める
                y = 0.299 * r.astype(np.float64) + 0.587 * g + 0.114 * b
                arr = np.clip(np.rint(y), 0, dtype_max(r.dtype)).astype(r.dtype)
                color_converted = True
                warnings.append(
                    "カラー画像をグレースケール（0.299R+0.587G+0.114B）に変換して測定します。"
                    "スキャナーはグレースケールモードで取り込むことを推奨します。"
                )
        else:
            raise ImageLoadError(
                f"多ページ／多チャンネル画像（形状 {arr.shape}）には対応していません。\n"
                "→ Fiji 等で1枚のグレースケール画像として保存してください。"
            )
    if arr.ndim != 2:
        raise ImageLoadError(f"2次元のグレースケール画像ではありません（形状 {arr.shape}）。")

    if arr.dtype == np.uint8:
        bit_depth = 8
    elif arr.dtype == np.uint16:
        bit_depth = 16
        if arr.max() <= 4095:
            warnings.append(
                "16-bit 画像ですが最大値が 4095 以下です。12-bit で取り込まれた可能性があります。"
                "飽和判定は 16-bit の上限（65535）で行うため、スキャナーの設定を確認してください。"
            )
    elif np.issubdtype(arr.dtype, np.integer):
        bit_depth = arr.dtype.itemsize * 8
        warnings.append(f"{arr.dtype} 型の画像です。通常の 8/16-bit とは異なるため注意してください。")
    else:
        raise ImageLoadError(
            f"浮動小数点型（{arr.dtype}）の画像には対応していません。\n→ 8-bit または 16-bit の整数画像で保存してください。"
        )

    if suffix in LOSSY_SUFFIXES:
        warnings.append(
            "JPEG は不可逆圧縮のため、画素値が元の値から変化しています。定量には TIFF または PNG を使ってください。"
        )
    arr.setflags(write=False)  # 測定用配列を誤って書き換えないようにする
    return LoadedImage(arr, bit_depth, str(arr.dtype), fmt, color_converted, warnings)
