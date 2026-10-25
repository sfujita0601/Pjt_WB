"""教材用合成データを examples/ に書き出す。

    python examples/make_examples.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wbtool.core.synthetic import example_dataset, make_blot  # noqa: E402

OUT = Path(__file__).resolve().parent
NOTE = "教材用合成データ（実験由来ではありません）"


def main() -> None:
    ds = example_dataset()
    files = {
        "synthetic_target_pERK_16bit.tif": ds["target"],
        "synthetic_loading_actin_16bit.tif": ds["loading"],
        "synthetic_target_pERK_overexposed_16bit.tif": ds["target_overexposed"],
    }
    for name, blot in files.items():
        tifffile.imwrite(OUT / name, blot.data, description="SYNTHETIC teaching data - not experimental")
    # 8-bit PNG（単純なバンド。値の検算用）
    simple = make_blot([40, 80, 120, 160], background=220, band_height=10, height=100)
    Image.fromarray(simple.data).save(OUT / "synthetic_simple_8bit.png")
    lines = ["sample_id,group,bio_rep,tech_rep,notes,image_id,lane_id"]
    for sid, g, bio, tech in ds["samples"]:
        lines.append(f"{sid},{g},{bio},{tech},{NOTE},,")
    (OUT / "synthetic_samples.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    truth = {
        "note": NOTE,
        "true_target_over_loading_ratio": dict(zip([s[0] for s in ds["samples"]], np.round(ds["true_ratio"], 4).tolist())),
        "loading_amount_relative": dict(zip([s[0] for s in ds["samples"]], ds["lc_load"].tolist())),
        "simple_8bit": {"background": 220, "amplitudes": [40, 80, 120, 160],
                        "band_rects_xywh": [r.as_tuple() for r in simple.bands],
                        "true_S_exact_background": simple.true_signal},
    }
    (OUT / "synthetic_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written to", OUT)


if __name__ == "__main__":
    main()
