"""プロジェクトのデータモデル。

GUI にも計算にも依存しない純粋なデータ構造。座標はすべて「原画像の画素座標」
（左上原点、x=列、y=行、整数）で保持し、表示倍率には依存しない。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

POLARITY_DARK = "dark"      # 背景より暗いバンド（フィルムのスキャン画像の通常の状態）
POLARITY_BRIGHT = "bright"  # 背景より明るいバンド（反転済み画像・蛍光画像など）

BG_NONE = "none"
BG_MANUAL = "manual_roi"
BG_LOCAL = "local"
BG_METHODS = (BG_LOCAL, BG_MANUAL, BG_NONE)
BG_LABELS = {
    BG_LOCAL: "同一レーンのバンド近傍（上下）",
    BG_MANUAL: "手動背景ROIの平均",
    BG_NONE: "背景補正なし",
}

ROLE_TARGET = "target"
ROLE_LOADING = "loading_control"
ROLE_PHOSPHO = "phospho"
ROLE_TOTAL = "total"
ROLE_OTHER = "other"
PROTEIN_ROLES = (ROLE_TARGET, ROLE_LOADING, ROLE_PHOSPHO, ROLE_TOTAL, ROLE_OTHER)
ROLE_LABELS = {
    ROLE_TARGET: "Target（目的タンパク質）",
    ROLE_LOADING: "ローディングコントロール（loading control）",
    ROLE_PHOSPHO: "リン酸化体（phospho）",
    ROLE_TOTAL: "総量（total）",
    ROLE_OTHER: "その他・未設定",
}

QC_PENDING = "未確認"
QC_DECISIONS = (QC_PENDING, "採用", "除外", "再撮影推奨")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Rect:
    """原画像座標の矩形。x, y は左上、w, h は画素数。"""

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        self.x, self.y, self.w, self.h = int(self.x), int(self.y), int(self.w), int(self.h)

    @property
    def area(self) -> int:
        return max(self.w, 0) * max(self.h, 0)

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    def intersects(self, other: "Rect") -> bool:
        return (
            self.x < other.x2 and other.x < self.x2
            and self.y < other.y2 and other.y < self.y2
        )

    def inside(self, width: int, height: int) -> bool:
        return self.x >= 0 and self.y >= 0 and self.x2 <= width and self.y2 <= height and self.w > 0 and self.h > 0

    def clipped(self, width: int, height: int) -> "Rect":
        x1, y1 = max(self.x, 0), max(self.y, 0)
        x2, y2 = min(self.x2, width), min(self.y2, height)
        return Rect(x1, y1, max(x2 - x1, 0), max(y2 - y1, 0))

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


@dataclass
class Lane:
    lane_id: str
    rect: Rect
    sample_id: str = ""


@dataclass
class BandROI:
    band_id: str
    lane_id: str
    rect: Rect
    protein: str = ""                 # 空なら画像の protein_target を使う
    bg_method_override: str = ""      # 空なら画像の設定を使う
    excluded: bool = False
    exclude_reason: str = ""


@dataclass
class ImageRecord:
    image_id: str
    path: str
    sha256: str
    width: int
    height: int
    bit_depth: int
    dtype: str
    file_format: str
    loaded_at: str
    color_converted: bool = False
    rel_path: str = ""                # プロジェクトファイルからの相対パス（再リンク用）
    protein_target: str = ""
    experiment_id: str = ""
    blot_id: str = ""
    membrane_id: str = ""
    exposure_id: str = ""
    polarity: str = POLARITY_DARK
    include: bool = True              # この画像を定量に使うか（同一膜の別露光は1つだけ使う）
    bg_method: str = BG_LOCAL
    bg_gap: int = 3                   # 近傍背景：バンドROIからの間隔（px）
    bg_height: int = 5                # 近傍背景：上下それぞれの帯の高さ（px）
    bg_rois: list[Rect] = field(default_factory=list)
    lanes: list[Lane] = field(default_factory=list)
    bands: list[BandROI] = field(default_factory=list)
    notes: str = ""

    def lane(self, lane_id: str) -> Lane | None:
        return next((l for l in self.lanes if l.lane_id == lane_id), None)

    def band(self, band_id: str) -> BandROI | None:
        return next((b for b in self.bands if b.band_id == band_id), None)

    def band_protein(self, band: BandROI) -> str:
        return band.protein or self.protein_target

    def band_bg_method(self, band: BandROI) -> str:
        return band.bg_method_override or self.bg_method

    def next_lane_id(self) -> str:
        return _next_id("L", [l.lane_id for l in self.lanes])

    def next_band_id(self) -> str:
        return _next_id("B", [b.band_id for b in self.bands])


@dataclass
class Sample:
    sample_id: str
    group: str = ""
    bio_rep: str = ""      # biological replicate ID（独立な単位。対応 t 検定ではこの ID で対応づける）
    tech_rep: str = ""     # technical replicate ID（同一試料の繰り返しロード等。独立な n に数えない）
    subject_id: str = ""   # 同一の実験単位（個体・同一試料）。対応のある検定はこの ID で対応づける
    batch_id: str = ""     # 実験日・ブロットなどのまとまり（記録用。これだけでは対応ありとみなさない）
    notes: str = ""


@dataclass
class QCDecision:
    decision: str = QC_PENDING
    reason: str = ""
    decided_at: str = ""


@dataclass
class HistoryEntry:
    timestamp: str
    action: str
    detail: str = ""


@dataclass
class AnalysisSettings:
    numerator_protein: str = ""
    denominator_protein: str = ""
    control_group: str = ""
    stat_group_a: str = ""
    stat_group_b: str = ""
    stat_test: str = "welch"          # 2群："welch" | "paired"（3群以上は stat_goal 等から決まる）
    stat_value: str = "ratio"         # "ratio" | "log2_ratio"（fold は ratio と同じ検定結果）
    stat_goal: str = "two"            # "two" | "vs_control" | "all_pairs"
    stat_design: str = "independent"  # "independent" | "paired"
    stat_equal_var: bool = False      # 3群以上で等分散を仮定するか（既定：仮定しない）
    stat_groups: list[str] = field(default_factory=list)   # 3群以上の比較に含める群
    figure: dict[str, Any] = field(default_factory=dict)
    checklist: dict[str, bool] = field(default_factory=dict)   # QC 学生チェックリストの確認状態
    protein_roles: dict[str, str] = field(default_factory=dict)  # タンパク質名 → 役割（ROLE_*）


@dataclass
class Project:
    name: str = "新規プロジェクト"
    created_at: str = field(default_factory=now_iso)
    modified_at: str = field(default_factory=now_iso)
    app_version: str = ""
    schema_version: int = SCHEMA_VERSION
    images: list[ImageRecord] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    qc_decisions: dict[str, QCDecision] = field(default_factory=dict)
    history: list[HistoryEntry] = field(default_factory=list)
    settings: AnalysisSettings = field(default_factory=AnalysisSettings)
    last_results: list[dict[str, Any]] = field(default_factory=list)

    # ---- 参照 ----
    def image(self, image_id: str) -> ImageRecord | None:
        return next((im for im in self.images if im.image_id == image_id), None)

    def sample(self, sample_id: str) -> Sample | None:
        return next((s for s in self.samples if s.sample_id == sample_id), None)

    def next_image_id(self) -> str:
        return _next_id("IMG", [im.image_id for im in self.images], width=3)

    def proteins(self) -> list[str]:
        names: list[str] = []
        for im in self.images:
            for b in im.bands:
                p = im.band_protein(b)
                if p and p not in names:
                    names.append(p)
            if im.protein_target and im.protein_target not in names:
                names.append(im.protein_target)
        return names

    def groups(self) -> list[str]:
        out: list[str] = []
        for s in self.samples:
            if s.group and s.group not in out:
                out.append(s.group)
        return out

    def log(self, action: str, detail: str = "") -> None:
        self.history.append(HistoryEntry(now_iso(), action, detail))

    # ---- 直列化 ----
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        d = dict(d)
        images = []
        for im in d.pop("images", []):
            im = dict(im)
            im["bg_rois"] = [Rect(**r) for r in im.get("bg_rois", [])]
            im["lanes"] = [Lane(lane_id=l["lane_id"], rect=Rect(**l["rect"]), sample_id=l.get("sample_id", ""))
                           for l in im.get("lanes", [])]
            im["bands"] = [BandROI(**{**b, "rect": Rect(**b["rect"])}) for b in im.get("bands", [])]
            images.append(ImageRecord(**_known(ImageRecord, im)))
        samples = [Sample(**_known(Sample, s)) for s in d.pop("samples", [])]
        qc = {k: QCDecision(**v) for k, v in d.pop("qc_decisions", {}).items()}
        history = [HistoryEntry(**h) for h in d.pop("history", [])]
        raw_settings = dict(d.pop("settings", {}))
        # 旧版（stat_design 導入前）で保存したプロジェクトの移行：対応 t を黙って Welch に変えない
        if "stat_design" not in raw_settings and raw_settings.get("stat_test") == "paired":
            raw_settings["stat_design"] = "paired"
        if raw_settings.get("stat_value") in ("fold", "log2_fold"):
            raw_settings["stat_value"] = "ratio" if raw_settings["stat_value"] == "fold" else "log2_ratio"
        settings = AnalysisSettings(**_known(AnalysisSettings, raw_settings))
        return cls(images=images, samples=samples, qc_decisions=qc, history=history,
                   settings=settings, **_known(cls, d))

    def save(self, path: str | os.PathLike) -> None:
        path = Path(path)
        self.modified_at = now_iso()
        for im in self.images:
            try:
                im.rel_path = os.path.relpath(im.path, path.parent)
            except ValueError:  # Windows の別ドライブ
                im.rel_path = ""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        path = Path(path)
        d = json.loads(path.read_text(encoding="utf-8"))
        proj = cls.from_dict(d)
        # 絶対パスが無ければ、プロジェクトファイルからの相対パスで探す
        for im in proj.images:
            if not Path(im.path).exists() and im.rel_path:
                cand = (path.parent / im.rel_path).resolve()
                if cand.exists():
                    im.path = str(cand)
        return proj


def _known(cls, d: dict[str, Any]) -> dict[str, Any]:
    names = set(cls.__dataclass_fields__)
    return {k: v for k, v in d.items() if k in names}


def _next_id(prefix: str, existing: list[str], width: int = 0) -> str:
    n = 1
    nums = []
    for e in existing:
        if e.startswith(prefix) and e[len(prefix):].isdigit():
            nums.append(int(e[len(prefix):]))
    if nums:
        n = max(nums) + 1
    return f"{prefix}{n:0{width}d}" if width else f"{prefix}{n}"
