"""教材用サンプルプロジェクトの作成（教材用合成データ。実験由来ではない）。"""
from __future__ import annotations

from pathlib import Path

import tifffile

from ..models.project import (
    ROLE_LOADING, ROLE_TARGET, BandROI, ImageRecord, Lane, Project, Rect, Sample, now_iso,
)
from .imageio import load_image, sha256_file
from .synthetic import example_dataset, example_dataset_3groups

EXAMPLE_NOTE = "教材用合成データ（実験由来ではありません）"


def build_example_project(folder: str | Path, with_rois: bool = True, kind: str = "2groups") -> Project:
    """folder に合成画像を書き出し、それを参照するプロジェクトを返す（保存はしない）。

    kind="2groups"：Control/Treated（過露光の例つき）、kind="3groups"：Control/DrugA/DrugB。
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if kind == "3groups":
        ds = example_dataset_3groups()
        p = Project(name="教材用サンプル・3群（合成データ）")
        specs = [
            ("synthetic3_target_pERK_16bit.tif", ds["target"], "pERK", "E1", True),
            ("synthetic3_loading_actin_16bit.tif", ds["loading"], "Actin", "E1", True),
        ]
    else:
        ds = example_dataset()
        p = Project(name="教材用サンプル（合成データ）")
        specs = [
            ("synthetic_target_pERK_16bit.tif", ds["target"], "pERK", "E1_short", True),
            ("synthetic_loading_actin_16bit.tif", ds["loading"], "Actin", "E1", True),
            ("synthetic_target_pERK_overexposed_16bit.tif", ds["target_overexposed"], "pERK", "E2_long", False),
        ]
    for sid, g, bio, tech in ds["samples"]:
        p.samples.append(Sample(sid, g, bio, tech, notes=EXAMPLE_NOTE))
    for fname, blot, protein, exposure, include in specs:
        path = folder / fname
        if not path.exists():
            tifffile.imwrite(path, blot.data, description="SYNTHETIC teaching data - not experimental")
        li = load_image(path)
        im = ImageRecord(p.next_image_id(), str(path.resolve()), sha256_file(path), li.data.shape[1],
                         li.data.shape[0], li.bit_depth, li.dtype, li.file_format, now_iso(),
                         protein_target=protein, experiment_id="EXP-demo", blot_id="BLOT1",
                         membrane_id="M1", exposure_id=exposure, include=include, notes=EXAMPLE_NOTE)
        if with_rois:
            for (lr, br), (sid, *_r) in zip(zip(blot.lanes, blot.bands), ds["samples"]):
                lid = im.next_lane_id()
                im.lanes.append(Lane(lid, lr, sid))
                # 合成バンドはぼかしてあるので、ROI を上下に 3 px 広げて裾を含める
                im.bands.append(BandROI(im.next_band_id(), lid, Rect(br.x, br.y - 3, br.w, br.h + 6)))
        p.images.append(im)
    p.settings.numerator_protein = "pERK"
    p.settings.denominator_protein = "Actin"
    p.settings.control_group = "Control"
    p.settings.protein_roles = {"pERK": ROLE_TARGET, "Actin": ROLE_LOADING}
    if kind == "3groups":
        p.settings.stat_goal = "vs_control"
        p.settings.stat_groups = ["Control", "DrugA", "DrugB"]
    else:
        p.settings.stat_group_a = "Control"
        p.settings.stat_group_b = "Treated"
    p.log("サンプル作成", EXAMPLE_NOTE)
    return p
