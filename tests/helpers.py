"""テスト用：合成画像からプロジェクトを組み立てる。"""
from pathlib import Path

import numpy as np
import tifffile

from wbtool.core.imageio import load_image, sha256_file
from wbtool.models.project import BandROI, ImageRecord, Lane, Project, Rect, Sample, now_iso


def add_image(project: Project, path: Path, data: np.ndarray, protein: str, membrane: str = "M1",
              exposure: str = "E1") -> ImageRecord:
    tifffile.imwrite(path, data)
    li = load_image(path)
    im = ImageRecord(project.next_image_id(), str(path), sha256_file(path), data.shape[1], data.shape[0],
                     li.bit_depth, li.dtype, li.file_format, now_iso(), protein_target=protein,
                     membrane_id=membrane, exposure_id=exposure)
    project.images.append(im)
    return im


def add_lanes_bands(im: ImageRecord, lanes: list[Rect], bands: list[Rect], sample_ids: list[str]) -> None:
    for lr, br, sid in zip(lanes, bands, sample_ids):
        lid = im.next_lane_id()
        im.lanes.append(Lane(lid, lr, sid))
        im.bands.append(BandROI(im.next_band_id(), lid, br))


def build_project(tmp_path: Path, target_amps, lc_amps, groups, bios, techs=None):
    from wbtool.core.synthetic import make_blot
    p = Project(name="test")
    n = len(target_amps)
    sids = [f"S{i + 1}" for i in range(n)]
    techs = techs or ["T1"] * n
    for sid, g, b, t in zip(sids, groups, bios, techs):
        p.samples.append(Sample(sid, g, b, t))
    t = make_blot(target_amps, background=50000, dtype=np.uint16)
    l = make_blot(lc_amps, background=50000, dtype=np.uint16)
    imt = add_image(p, tmp_path / "t.tif", t.data, "Target")
    iml = add_image(p, tmp_path / "l.tif", l.data, "Actin")
    add_lanes_bands(imt, t.lanes, t.bands, sids)
    add_lanes_bands(iml, l.lanes, l.bands, sids)
    return p, t, l
