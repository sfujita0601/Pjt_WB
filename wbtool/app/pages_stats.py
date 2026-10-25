"""［統計］画面：質問に答えて手法を選ぶ案内つき。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core.stats import describe
from ..core.stats_runner import DESIGNS, GOALS, VALUES, run_stats, stats_table_csv, value_column
from .terms import TERMS
from .widgets import DataFrameModel, HintBox, LazyRefreshMixin, fmt, make_table_view, page_header


class StatsPage(LazyRefreshMixin, QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._building = False
        self._outcome = None
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("統計（statistics）",
                                  "生物学的反復（biological replicate）を独立な単位（n）として比較します。"
                                  "技術的反復（technical replicate）は平均済みで n に数えません。"))
        self.hint = HintBox(
            "<b>n はバンドの数ではなく、独立な生物学的反復の数</b>です。下の①〜④に答えると、手法が決まります。"
            "3群以上を2群ずつ t 検定で比べると偽陽性が増えるため、多重比較で p 値を調整します。"
            "p 値だけでなく、差の大きさと 95% 信頼区間を見てください。", lesson=5)
        lay.addWidget(self.hint)

        q = QGroupBox("手法の案内")
        grid = QGridLayout(q)
        grid.addWidget(QLabel("<b>① 何を比べたいですか？</b>"), 0, 0)
        self.grp_goal = QButtonGroup(self)
        for i, (k, v) in enumerate(GOALS.items()):
            rb = QRadioButton(v)
            rb.setProperty("key", k)
            self.grp_goal.addButton(rb, i)
            grid.addWidget(rb, 0, i + 1)
        grid.addWidget(QLabel("<b>② 群どうしの関係は？</b>"), 1, 0)
        self.grp_design = QButtonGroup(self)
        for i, (k, v) in enumerate(DESIGNS.items()):
            rb = QRadioButton(v)
            rb.setProperty("key", k)
            self.grp_design.addButton(rb, i)
            grid.addWidget(rb, 1, i + 1, 1, 1 if i == 0 else 2)
        grid.addWidget(QLabel("<b>③ 検定に使う値</b>"), 2, 0)
        self.cmb_val = QComboBox()
        for k, v in VALUES.items():
            self.cmb_val.addItem(v, k)
        self.cmb_val.setToolTip("対照群を 1 とした相対値（fold）は正規化比を定数で割っただけなので、検定結果は正規化比と同じです。")
        grid.addWidget(self.cmb_val, 2, 1)
        self.chk_eqvar = QCheckBox("3群以上で各群の分散が等しいと仮定する（通常はオフ：Welch ANOVA＋Games–Howell）")
        grid.addWidget(self.chk_eqvar, 2, 2, 1, 2)
        grid.addWidget(QLabel("<b>④ 比べる群</b>"), 3, 0)
        self.w_two = QWidget()
        h = QHBoxLayout(self.w_two)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("群 A："))
        self.cmb_a = QComboBox()
        h.addWidget(self.cmb_a)
        h.addWidget(QLabel("群 B："))
        self.cmb_b = QComboBox()
        h.addWidget(self.cmb_b)
        h.addStretch(1)
        grid.addWidget(self.w_two, 3, 1, 1, 3)
        self.lst_groups = QListWidget()
        self.lst_groups.setMaximumHeight(90)
        self.lst_groups.setFlow(QListWidget.LeftToRight)
        self.lst_groups.setWrapping(True)
        grid.addWidget(self.lst_groups, 4, 1, 1, 3)
        self.lbl_method = QLabel()
        self.lbl_method.setWordWrap(True)
        self.lbl_method.setStyleSheet("background:#EEF6EE; border:1px solid #A8D0A8; padding:6px;")
        grid.addWidget(self.lbl_method, 5, 0, 1, 4)
        lay.addWidget(q)

        for b in self.grp_goal.buttons() + self.grp_design.buttons():
            b.toggled.connect(lambda on: on and self._on_settings())
        self.cmb_val.activated.connect(self._on_settings)
        self.chk_eqvar.toggled.connect(self._on_settings)
        self.cmb_a.activated.connect(self._on_settings)
        self.cmb_b.activated.connect(self._on_settings)
        self.lst_groups.itemChanged.connect(lambda _it: self._on_settings())

        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)
        g = QWidget()
        v = QVBoxLayout(g)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel("<b>検定前の確認</b>：解析単位（生物学的反復）と群・実験単位・使用したバンドの割当て"))
        self.assign_model = DataFrameModel(headers=TERMS)
        v.addWidget(make_table_view(self.assign_model))
        row = QHBoxLayout()
        self.chk_confirm = QCheckBox("上の表で、群・生物学的反復・実験単位ID・使用したバンド（画像/膜）の割当てが正しいことを確認した")
        self.chk_confirm.toggled.connect(self._render)
        row.addWidget(self.chk_confirm, 1)
        b = QPushButton("結果を CSV に保存…")
        b.clicked.connect(self._export)
        row.addWidget(b)
        v.addLayout(row)
        split.addWidget(g)
        self.txt = QTextBrowser()
        split.addWidget(self.txt)
        split.setSizes([260, 420])
        ctrl.changed.connect(self.request_refresh)
        ctrl.projectReplaced.connect(lambda: self.chk_confirm.setChecked(False))

    def set_learning(self, on: bool) -> None:
        self.hint.setVisible(on)

    # ------------------------------------------------------------ 表示
    def refresh(self) -> None:
        s = self.ctrl.project.settings
        res = self.ctrl.normalization()
        groups = list(dict.fromkeys(res.bio_table["group"])) if len(res.bio_table) else []
        self._building = True
        try:
            for b in self.grp_goal.buttons():
                b.setChecked(b.property("key") == s.stat_goal)
            for b in self.grp_design.buttons():
                b.setChecked(b.property("key") == s.stat_design)
            self.cmb_val.setCurrentIndex(max(self.cmb_val.findData(value_column(s)), 0))
            self.chk_eqvar.setChecked(s.stat_equal_var)
            for cmb, cur in ((self.cmb_a, s.stat_group_a), (self.cmb_b, s.stat_group_b)):
                cmb.clear()
                cmb.addItem("（選択）", "")
                for g in groups:
                    cmb.addItem(g, g)
                cmb.setCurrentIndex(max(cmb.findData(cur), 0))
            self.lst_groups.clear()
            chosen = s.stat_groups or groups
            for g in groups:
                it = QListWidgetItem(g + ("（対照群）" if g == s.control_group else ""))
                it.setData(Qt.UserRole, g)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if g in chosen else Qt.Unchecked)
                self.lst_groups.addItem(it)
            two = s.stat_goal == "two"
            self.w_two.setVisible(two)
            self.lst_groups.setVisible(not two)
            self.chk_eqvar.setEnabled(not two)
        finally:
            self._building = False
        bio = res.bio_table.copy()
        if len(bio):
            st = res.sample_table.set_index("sample_id")
            bio["使用バンド（分子 | 分母）"] = bio["sample_ids"].map(
                lambda ids: "; ".join(f"{st.loc[i, 'num_band']} | {st.loc[i, 'den_band']}" for i in ids.split(",") if i in st.index))
            bio["膜（分子/分母）"] = bio["sample_ids"].map(
                lambda ids: ", ".join(sorted({f"{st.loc[i, 'num_membrane']}/{st.loc[i, 'den_membrane']}" for i in ids.split(",") if i in st.index})))
            bio = bio[["group", "bio_rep", "subject_id", "batch_id", "n_tech", "ratio", "log2_ratio", "fold",
                       "使用バンド（分子 | 分母）", "膜（分子/分母）", "sample_ids"]]
        self.assign_model.set_frame(bio)
        self._render()

    def _method_text(self) -> str:
        s = self.ctrl.project.settings
        if s.stat_goal == "two":
            m = "対応のある t 検定（subject_id で対応づけ）" if s.stat_design == "paired" else "Welch の t 検定（等分散を仮定しない）"
        elif s.stat_design == "paired":
            m = "対応のある3群以上（反復測定 ANOVA／混合モデル）は未実装のため、検定しません。"
        elif s.stat_goal == "vs_control":
            m = ("一元配置 ANOVA ＋ Dunnett（対照群との比較）" if s.stat_equal_var
                 else "Welch ANOVA ＋ Games–Howell（対照群を含む対のみ表示）")
        else:
            m = "一元配置 ANOVA ＋ Tukey（全群間）" if s.stat_equal_var else "Welch ANOVA ＋ Games–Howell（全群間）"
        return f"<b>選ばれる手法：</b>{m}　／　<b>値：</b>{VALUES[value_column(s)]}"

    def _render(self) -> None:
        if self._building:
            return
        s = self.ctrl.project.settings
        res = self.ctrl.normalization()
        bio = res.bio_table
        val = value_column(s)
        self.lbl_method.setText(self._method_text())
        h = []
        if res.blocking:
            h.append("<p style='color:#B00000'><b>⛔ 統計を止めています：</b>" + " ".join(res.blocking) + "</p>")
        h.append(f"<h3>記述統計（生物学的反復ごと、{VALUES[val]}）</h3><table border='1' cellspacing='0' cellpadding='3'>"
                 "<tr><th>群</th><th>n</th><th>平均</th><th>SD</th><th>SEM</th><th>95%CI</th><th>中央値</th><th>最小–最大</th></tr>")
        for g in (list(dict.fromkeys(bio["group"])) if len(bio) else []):
            d = describe(bio.loc[bio["group"] == g, val], g)
            h.append(f"<tr><td>{g}</td><td>{d.n}</td><td>{fmt(d.mean)}</td><td>{fmt(d.sd)}</td><td>{fmt(d.sem)}</td>"
                     f"<td>{fmt(d.ci_low)} – {fmt(d.ci_high)}</td><td>{fmt(d.median)}</td><td>{fmt(d.minimum)} – {fmt(d.maximum)}</td></tr>")
        h.append("</table>")
        if val == "log2_ratio" and len(bio) and (bio["log2_status"] != "OK").any():
            h.append("<p style='color:#B00000'>log2 変換できない生物学的反復があります（比が 0 以下または欠損）。"
                     "0 や負の値を擬似的な正の値に置き換えることはしません。［正規化］の log2_status 列を確認してください。</p>")
        h.append("<h3>検定</h3>")
        self._outcome = None
        if not self.chk_confirm.isChecked():
            h.append("<p><b>検定結果を表示するには、上の割当て表を確認してチェックを入れてください。</b></p>")
        else:
            out = run_stats(s, res)
            self._outcome = out if out.ok else None
            if not out.ok:
                h.append(f"<p style='color:#B00000'><b>検定しませんでした：</b>{out.refusal}</p>")
            elif out.kind == "two":
                r = out.two
                h.append(f"<p><b>{r.test_name}</b>　{r.group_a}（n={r.n_a}） vs {r.group_b}（n={r.n_b}）"
                         + (f"、対の数 {r.n_pairs}" if s.stat_design == "paired" else "") + "</p>"
                         f"<ul><li>独立な単位：生物学的反復（bio_rep）" + ("、対応は実験単位ID（subject_id）" if s.stat_design == "paired" else "") + "</li>"
                         f"<li>差（{r.group_b} − {r.group_a}）＝ {fmt(r.mean_diff)}、95% 信頼区間 [{fmt(r.ci_low)}, {fmt(r.ci_high)}]"
                         + (f"（倍率 2^差 ＝ {fmt(2 ** r.mean_diff)}）" if val == "log2_ratio" else "") + "</li>"
                         f"<li>t = {fmt(r.statistic)}、自由度 = {fmt(r.df)}、p = {fmt(r.p_value)}（両側）</li>"
                         f"<li>効果量：{r.effect_name} = {fmt(r.effect_size)}</li>"
                         f"<li>欠損・除外された値：{r.n_missing}</li></ul>")
            else:
                m = out.multi
                ns = "、".join(f"{g} n={n}" for g, n in m.n.items())
                h.append(f"<p><b>{m.omnibus_name}</b>：F({fmt(m.df1)}, {fmt(m.df2)}) = {fmt(m.F)}、p = {fmt(m.p_omnibus)}、"
                         f"η² = {fmt(m.eta_squared)}（{ns}）</p>")
                h.append(f"<p><b>多重比較：</b>{m.posthoc_name}</p><table border='1' cellspacing='0' cellpadding='3'>"
                         "<tr><th>比較</th><th>差（B − A）</th><th>95%CI（同時）</th><th>調整済み p</th></tr>")
                for r in m.pairs.itertuples():
                    extra = f"（倍率 {fmt(2 ** r.diff)}）" if val == "log2_ratio" else ""
                    h.append(f"<tr><td>{r.group_b} vs {r.group_a}</td><td>{fmt(r.diff)}{extra}</td>"
                             f"<td>{fmt(r.ci_low)} – {fmt(r.ci_high)}</td><td>{fmt(r.p_adj)}</td></tr>")
                h.append(f"</table><p>欠損・除外された値：{m.n_missing}</p>")
            if out.notes:
                h.append("<p><b>検定の前提・注意</b></p><ul>" + "".join(f"<li>{n}</li>" for n in out.notes) + "</ul>")
            if out.ok:
                h.append(f"<p style='color:#555'><b>図の説明文に使う記述：</b>{out.description}</p>")
        self.txt.setHtml("".join(h))

    # ------------------------------------------------------------ 設定
    def _on_settings(self) -> None:
        if self._building:
            return
        s = self.ctrl.project.settings
        goal = next((b.property("key") for b in self.grp_goal.buttons() if b.isChecked()), s.stat_goal)
        design = next((b.property("key") for b in self.grp_design.buttons() if b.isChecked()), s.stat_design)
        groups = [self.lst_groups.item(i).data(Qt.UserRole) for i in range(self.lst_groups.count())
                  if self.lst_groups.item(i).checkState() == Qt.Checked]
        new = dict(stat_goal=goal, stat_design=design, stat_value=self.cmb_val.currentData() or "ratio",
                   stat_equal_var=self.chk_eqvar.isChecked(), stat_group_a=self.cmb_a.currentData() or "",
                   stat_group_b=self.cmb_b.currentData() or "", stat_groups=groups)
        old = {k: getattr(s, k) for k in new}
        if new != old:
            for k, v in new.items():
                setattr(s, k, v)
            self.ctrl.commit("統計設定の変更", ", ".join(f"{k}={v}" for k, v in new.items() if old[k] != v))

    def _export(self) -> None:
        if self._outcome is None:
            QMessageBox.information(self, "結果の保存", "保存できる検定結果がありません。割当てを確認してチェックを入れ、検定を実行してください。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "統計結果 CSV", "stats_results.csv", "CSV (*.csv)")
        if path:
            stats_table_csv(self._outcome, path)
            self.ctrl.project.log("統計結果を保存", path)

