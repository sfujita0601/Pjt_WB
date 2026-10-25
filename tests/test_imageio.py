import numpy as np
import pytest
import tifffile
from PIL import Image

from wbtool.core.imageio import ImageLoadError, load_image, sha256_file


def test_tiff_16bit(tmp_path):
    arr = (np.arange(200, dtype=np.uint16) * 300).reshape(10, 20)
    p = tmp_path / "x.tif"
    tifffile.imwrite(p, arr)
    img = load_image(p)
    assert img.bit_depth == 16 and img.data.dtype == np.uint16
    assert np.array_equal(img.data, arr)


def test_png_8bit_and_16bit(tmp_path):
    a8 = np.random.default_rng(0).integers(0, 256, (12, 7), dtype=np.uint8)
    Image.fromarray(a8).save(tmp_path / "a.png")
    i8 = load_image(tmp_path / "a.png")
    assert i8.bit_depth == 8 and np.array_equal(i8.data, a8)
    a16 = (np.arange(84, dtype=np.uint16) * 700).reshape(12, 7)
    Image.fromarray(a16).save(tmp_path / "b.png")
    i16 = load_image(tmp_path / "b.png")
    assert i16.bit_depth == 16 and np.array_equal(i16.data, a16)


def test_jpeg_warning(tmp_path):
    Image.fromarray(np.full((10, 10), 128, np.uint8)).save(tmp_path / "a.jpg")
    img = load_image(tmp_path / "a.jpg")
    assert any("JPEG" in w for w in img.warnings)


def test_rgb_gray_identical_channels(tmp_path):
    g = np.random.default_rng(1).integers(0, 256, (6, 6), dtype=np.uint8)
    Image.fromarray(np.dstack([g, g, g])).save(tmp_path / "rgb.png")
    img = load_image(tmp_path / "rgb.png")
    assert not img.color_converted and np.array_equal(img.data, g)


def test_missing_file_message(tmp_path):
    with pytest.raises(ImageLoadError) as e:
        load_image(tmp_path / "none.tif")
    assert "再リンク" in str(e.value)


def test_hash_unchanged_by_loading(tmp_path):
    p = tmp_path / "x.tif"
    tifffile.imwrite(p, np.ones((4, 4), np.uint16))
    h = sha256_file(p)
    load_image(p)
    assert sha256_file(p) == h
