"""主窗口。

界面结构对齐参考应用的「一键生成配置命令」模块：

    顶栏       —— 标题、浅色/深色、关于
    页面选项卡 —— 一键生成配置命令 / 设备命令详解 / 核验说明 / 使用说明
    ├ 左栏     —— 一键配置脚本：一个大输出框，所有生成结果都落在这里
    └ 右栏     —— 固定的设备厂商选择条 + 一页到底的参数区
                   每个场景一个参数区，各自带一个「生成脚本」按钮，
                   点哪个就把哪一段追加进左边的脚本框。

这样跟参考应用一样：参数全在一页上，想看哪块就看哪块，
生成也能按块来，不必来回切换场景。
"""
from __future__ import annotations

import pathlib
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import (__version__, audit, blueprint, changekit, compose, convert,
                diagnose, diffcfg, explain, project, registry, rollback, verify)
from ..model import GenResult, Kind
from ..vendors import VENDORS, generate, supports
from . import ai_page
from . import dpi
from . import dnd
from . import theme as T

APP_NAME = "Yang-SwitchKit"
APP_VERSION = "V" + __version__

PAGE_MAIN = "一键生成配置命令"
PAGE_DOC = "设备命令详解"
PAGE_AI = "AI 助手"
PAGE_AUDIT = "配置体检"
PAGE_DIFF = "配置对比"
PAGE_DIAG = "故障排查"
PAGE_VERIFY = "核验说明"
PAGE_HELP = "使用说明"

#: 脚本区的三种输出视图
MODE_CONFIG = "config"
MODE_BLUEPRINT = "blueprint"
MODE_ROLLBACK = "rollback"

SCRIPT_MODES = (
    (MODE_CONFIG, "配置脚本", "▌ 一键配置脚本"),
    (MODE_BLUEPRINT, "整机开局", "▌ 整机开局脚本"),
    (MODE_ROLLBACK, "回滚脚本", "▌ 回滚脚本"),
)
MODE_TITLES = {mode: title for mode, _label, title in SCRIPT_MODES}

DOC_DETAIL = "本次生成的命令详解"
DOC_MANUAL = "该厂商命令手册"
DOC_COMPARE = "四厂商命令对照"
DOC_MODES = (DOC_DETAIL, DOC_MANUAL, DOC_COMPARE)

#: 脚本框里独占一行的分隔线
RULE = "#" + "-" * 68


class App(tk.Tk):
    """四厂商交换机配置命令生成器。"""

    #: 四厂商对照行：缩进 + 厂商名 + 空格 + 语法
    _CMP_RX = re.compile(r"^( {6})(H3C|华为|思科|锐捷) +(.*)$")

    def __init__(self) -> None:
        dpi.enable_awareness()
        super().__init__()

        self.scale = dpi.scale_for(self)
        self.dark = False          # 默认浅色
        T.apply(self, self.scale, self.dark)

        self.title(f"{APP_NAME} · 交换机配置命令一键生成")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(1460 * self.scale), sw - T.px(40))
        h = min(int(900 * self.scale), sh - T.px(90))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.minsize(int(1120 * self.scale), int(680 * self.scale))

        # ---- 方案文件状态 --------------------------------------------
        self.project_path: pathlib.Path | None = None   # 当前方案文件，None 表示还没存过
        self.dirty = False          # 有没有未保存的改动
        self._silent = False        # 回填 / 加载方案期间，不要让 trace 标脏

        # ---- 跨主题重建需要保留的状态 --------------------------------
        self.vendor_key: str = VENDORS[0].key
        self.vendor_var = tk.StringVar(value=self.vendor_key)
        self.show_comment = tk.BooleanVar(value=True)
        self.doc_mode = tk.StringVar(value=DOC_DETAIL)
        self.script_mode: str = MODE_CONFIG
        self._last_rollback = None     # 回滚脚本的上一次推导结果，供状态栏统计
        self._last_blueprint = None    # 整机开局的上一次编排结果
        self.baseline = None           # 变更前配置（rollback.Baseline）
        self.baseline_source: str = "" # 粘贴的原文，切主题重建时回填
        self.audit_source: str = ""    # 体检页粘贴的原文
        self.diff_a_source: str = ""   # 对比页 A 侧原文
        self.diff_b_source: str = ""   # 对比页 B 侧原文

        self.store: dict = {}          # (厂商, 场景) -> {参数: 值}
        self.results: dict = {}        # 场景 key -> GenResult（当前厂商）
        self.errors: dict = {}         # 场景 key -> 出错信息
        self.order: list = []          # 已经生成过的场景，按生成顺序

        # ---- 每次重建都要刷新的控件索引 ------------------------------
        self.widgets: dict = {}        # 场景 key -> {参数 key: (类型, 控件)}
        self.choice_maps: dict = {}    # 场景 key -> {参数 key: {显示文本: 取值}}
        self.help_labels: list = []    # [(标签控件, 跨列数)]，随宽度调整折行
        self.cards: dict = {}          # 场景 key -> 状态标签
        self.gen_buttons: dict = {}    # 场景 key -> 「生成脚本」按钮
        self.card_frames: dict = {}    # 场景 key -> 卡片外框，AI 回填后闪一下用它
        self.ai_page = None            # AI 助手页，见 ui/ai_page.py

        # ---- 配置体检的第二、三层（语法校对 / 在线语义） -------------
        self._syntax = None            # (findings, unmatched)，见 ai/rules.py
        self._ai_result = None         # 在线语义补充的结果，见 ai/advise.py
        self._audit_queue: queue.Queue = queue.Queue()
        self._audit_busy = False

        # 上一次「一键转换」的说明，贴在脚本区最前面；手工切厂商时清空
        self.convert_note: list = []   # [str]，见 convert.heading_lines()

        # 从「AI 助手 → 从现网导入」读到的现状，导变更包时写进「变更前状态」
        self._imported_snap = None

        self._status_timer = None
        self._sash_timer = None

        self.columnconfigure(0, weight=1)
        self._build_all()
        # 关窗口前问一句有没有没保存的改动，避免白填一屏参数
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 把方案文件拖进窗口就能打开。挂不上也不影响用，按钮和命令行都还在
        self.drop = dnd.FileDrop(self, self._on_files_dropped)
        self.drop.enable()

    # ==================================================================
    # 骨架
    # ==================================================================

    def _build_all(self) -> None:
        # 切主题会把所有控件重建一遍，AI 页里用户敲了一半的话不能丢
        snapshot = self._snapshot_ai()

        for child in list(self.children.values()):
            child.destroy()

        self.widgets.clear()
        self.choice_maps.clear()
        self.help_labels.clear()
        self.cards.clear()
        self.gen_buttons.clear()
        self.card_frames.clear()
        self.ai_page = None

        self.rowconfigure(2, weight=1)
        self._build_banner()
        tk.Frame(self, background=T.BORDER, height=1).grid(row=1, column=0, sticky="ew")
        self._build_pages()
        self._build_status()

        self._restore_ai(snapshot)
        self._restore_fields()
        self._sync_gen_buttons()
        self._bind_dirty_tracking()
        self._bind_shortcuts()
        self._refresh_output()
        self._refresh_doc()
        self._refresh_title()

    def _build_banner(self) -> None:
        banner = tk.Frame(self, background=T.PANEL, padx=T.px(18), pady=T.px(12))
        banner.grid(row=0, column=0, sticky="ew")
        banner.columnconfigure(1, weight=1)

        left = tk.Frame(banner, background=T.PANEL)
        left.grid(row=0, column=0, sticky="w")
        tk.Frame(left, background=T.ACCENT, width=T.px(4)).pack(side="left", fill="y",
                                                               padx=(0, T.px(12)))
        titles = tk.Frame(left, background=T.PANEL)
        titles.pack(side="left")
        ttk.Label(titles, text=APP_NAME,
                  style="BannerTitle.TLabel").pack(anchor="w")
        # 场景数不要写死，从注册表取，加了场景这里自动跟着变
        ttk.Label(titles, text="交换机配置命令一键生成　|　思科 · 华为 · H3C · 锐捷　|　"
                              f"{len(registry.SCENARIOS)} 个配置场景 · 逐条命令详解",
                  style="BannerSub.TLabel").pack(anchor="w", pady=(T.px(3), 0))

        bar = tk.Frame(banner, background=T.PANEL)
        bar.grid(row=0, column=2, sticky="e")
        ttk.Button(bar, text="打开方案", style="Ghost.TButton",
                   command=self._open_project).pack(side="left", padx=(0, T.px(6)))
        ttk.Button(bar, text="保存方案", style="Ghost.TButton",
                   command=self._save_project).pack(side="left", padx=(0, T.px(6)))
        ttk.Button(bar, text="浅色" if self.dark else "深色", style="Ghost.TButton",
                   command=self._toggle_theme).pack(side="left", padx=(0, T.px(6)))
        ttk.Button(bar, text="关于", style="Ghost.TButton",
                   command=self._about).pack(side="left")

    def _build_pages(self) -> None:
        self.pages = ttk.Notebook(self, style="Page.TNotebook")
        self.pages.grid(row=2, column=0, sticky="nsew", padx=T.px(10), pady=T.px(8))

        self.page_main = tk.Frame(self.pages, background=T.BG)
        self.page_doc = tk.Frame(self.pages, background=T.BG)
        self.page_ai = tk.Frame(self.pages, background=T.BG)
        self.page_audit = tk.Frame(self.pages, background=T.BG)
        self.page_diff = tk.Frame(self.pages, background=T.BG)
        self.page_diag = tk.Frame(self.pages, background=T.BG)
        self.page_verify = tk.Frame(self.pages, background=T.BG)
        self.page_help = tk.Frame(self.pages, background=T.BG)
        self.pages.add(self.page_main, text=PAGE_MAIN)
        self.pages.add(self.page_doc, text=PAGE_DOC)
        self.pages.add(self.page_ai, text=PAGE_AI)
        self.pages.add(self.page_audit, text=PAGE_AUDIT)
        self.pages.add(self.page_diff, text=PAGE_DIFF)
        self.pages.add(self.page_diag, text=PAGE_DIAG)
        self.pages.add(self.page_verify, text=PAGE_VERIFY)
        self.pages.add(self.page_help, text=PAGE_HELP)

        self._build_main_page(self.page_main)
        self._build_doc_page(self.page_doc)
        self.ai_page = ai_page.AIPage(self, self.page_ai)
        self._build_audit_page(self.page_audit)
        self._build_diff_page(self.page_diff)
        self._build_diag_page(self.page_diag)
        self._build_text_page(self.page_verify, verify.render_note())
        self._build_text_page(self.page_help, self._help_lines())

    # ------------------------------------------------------------------
    # AI 页在切主题重建时的状态保存
    # ------------------------------------------------------------------

    def _snapshot_ai(self):
        page = self.ai_page
        if page is None:
            return None
        try:
            return {
                "intent": page.intent_input.get("1.0", "end").rstrip("\n"),
                "local": page._local_text,
                "peer": page._peer_text,
                "peer_label": page._peer_label,
                "ctx": page._pair_ctx,
            }
        except tk.TclError:
            return None

    def _restore_ai(self, snapshot) -> None:
        if not snapshot or self.ai_page is None:
            return
        page = self.ai_page
        try:
            page.intent_input.delete("1.0", "end")
            page.intent_input.insert("1.0", snapshot.get("intent", ""))

            page._local_text = snapshot.get("local", "")
            page._peer_text = snapshot.get("peer", "")
            page._peer_label = snapshot.get("peer_label", "")
            page._pair_ctx = snapshot.get("ctx")
            page._render_commands()
            page._refresh_peer_btn()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # AI 回填之后的两下反馈：滚过去 + 闪一下
    # ------------------------------------------------------------------

    def _scroll_to_card(self, scenario_key: str) -> None:
        """把参数区滚到指定场景。AI 改完参数不滚过去，用户根本看不见。"""
        frame = self.card_frames.get(scenario_key)
        if frame is None:
            return
        self.canvas.update_idletasks()
        total = self.form.winfo_height()
        if total <= 0:
            return
        self.canvas.yview_moveto(max(0.0, min(1.0, frame.winfo_y() / total)))

    def _flash_card(self, scenario_key: str) -> None:
        """卡片外框闪两下，告诉用户「改的是这一块」。"""
        frame = self.card_frames.get(scenario_key)
        if frame is None:
            return

        def blink(left: int) -> None:
            try:
                frame.configure(background=T.ACCENT if left % 2 else T.BORDER_SOFT)
            except tk.TclError:
                return
            if left > 0:
                self.after(240, lambda: blink(left - 1))

        blink(4)

    def _build_status(self) -> None:
        self.status = ttk.Label(self, text="", style="Faint.TLabel", anchor="w")
        self.status.grid(row=3, column=0, sticky="ew", padx=T.px(18), pady=(0, T.px(9)))
        self._set_status()

    # ==================================================================
    # 页面一：一键生成配置命令
    # ==================================================================

    def _build_main_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(page, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew")
        self.paned = paned

        self._build_script_pane(paned)
        self._build_param_pane(paned)

        # 分栏初始比例放在窗口显示之后再设，否则 sashpos 还拿不到实际宽度
        if self._sash_timer:
            self.after_cancel(self._sash_timer)
        self._sash_timer = self.after(120, self._set_sash)

    def _set_sash(self) -> None:
        self._sash_timer = None
        try:
            width = self.paned.winfo_width()
            if width > T.px(600):
                self.paned.sashpos(0, min(T.px(470), width - T.px(520)))
        except tk.TclError:
            pass

    def _build_script_pane(self, parent) -> None:
        pane = tk.Frame(parent, background=T.PANEL)
        parent.add(pane, weight=0)
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(2, weight=1)

        # 标题条：参考应用左栏那个「一键配置脚本」页签
        head = tk.Frame(pane, background=T.ACCENT)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.script_title = ttk.Label(
            head, text=MODE_TITLES.get(self.script_mode, "▌ 一键配置脚本"),
            style="ScriptTab.TLabel")
        self.script_title.pack(side="left")
        self.script_meta = ttk.Label(head, text="", style="ScriptTab.TLabel")
        self.script_meta.pack(side="right", padx=(0, T.px(10)))

        # 输出视图切换：配置脚本 / 回滚脚本
        #
        # 这里不用 ttk.Button —— 它的背景色由主题引擎托管，深色主题下未选中的
        # 那个按钮会和面板底色糊在一起，整个按钮"消失"。用 tk.Label 自己画，
        # 背景色完全可控。
        bar = tk.Frame(pane, background=T.PANEL)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew",
                 padx=T.px(8), pady=(T.px(6), T.px(2)))
        self.mode_buttons = {}
        for mode, label, _title in SCRIPT_MODES:
            tab = tk.Label(bar, text=label, font=T.FONT_UI,
                           padx=T.px(14), pady=T.px(6), cursor="hand2",
                           borderwidth=0)
            tab.pack(side="left", padx=(0, T.px(6)))
            tab.bind("<Button-1>", lambda e, m=mode: self._set_script_mode(m))
            self.mode_buttons[mode] = tab

        self.baseline_btn = tk.Label(
            bar, text="变更前配置", font=T.FONT_UI_SM, cursor="hand2",
            padx=T.px(10), pady=T.px(5), borderwidth=0)
        self.baseline_btn.bind("<Button-1>", lambda e: self._open_baseline())
        self._paint_mode_buttons()

        self.out = tk.Text(pane, background=T.PANEL, foreground=T.C_CMD,
                           insertbackground=T.FG, font=T.FONT_MONO,
                           relief="flat", bd=0, wrap="none",
                           padx=T.px(12), pady=T.px(10),
                           selectbackground=T.SEL_BG, selectforeground=T.FG)
        self.out.grid(row=2, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(pane, orient="vertical", command=self.out.yview)
        vbar.grid(row=2, column=1, sticky="ns")
        hbar = ttk.Scrollbar(pane, orient="horizontal", command=self.out.xview)
        hbar.grid(row=3, column=0, sticky="ew")
        self.out.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.out.configure(state="disabled")

        self.out.tag_configure("sep", foreground=T.C_NOTE)
        self.out.tag_configure("head", foreground=T.C_HEAD,
                               font=("Consolas", 10, "bold"))
        self.out.tag_configure("sub", foreground=T.C_BLOCK,
                               font=("Consolas", 10, "bold"))
        self.out.tag_configure("cmd", foreground=T.C_CMD)
        self.out.tag_configure("cmt", foreground=T.C_CMT)
        self.out.tag_configure("note", foreground=T.C_NOTE)
        self.out.tag_configure("warn", foreground=T.C_WARN)
        self.out.tag_configure("err", foreground=T.C_ERR)

        foot = tk.Frame(pane, background=T.PANEL)
        foot.grid(row=4, column=0, columnspan=2, sticky="ew",
                  padx=T.px(10), pady=(T.px(4), T.px(10)))
        ttk.Button(foot, text="复制脚本", style="Accent.TButton",
                   command=self._copy).pack(side="left")
        ttk.Button(foot, text="导出", style="Ghost.TButton",
                   command=self._export).pack(side="left", padx=T.px(6))
        # 交付用：命令 + 回滚 + 验收 + 影响范围打成一份，写变更单直接贴
        ttk.Button(foot, text="变更包", style="Ghost.TButton",
                   command=self._export_changekit).pack(side="left",
                                                        padx=(0, T.px(6)))
        ttk.Button(foot, text="清空", style="Ghost.TButton",
                   command=self._clear_all).pack(side="left")
        ttk.Checkbutton(foot, text="保留注释", variable=self.show_comment,
                        style="Card.TCheckbutton",
                        command=self._toggle_comments).pack(side="right")

    def _toggle_comments(self) -> None:
        keep = self.show_comment.get()
        self._refresh_output(keep_scroll=True)
        self._mark_dirty()
        if keep:
            self._flash("已恢复注释：分节标题、说明与风险提示都会出现在脚本里")
        else:
            self._flash("已去掉全部注释（# 与 !），脚本区现在是可以直接整段粘贴的纯命令")

    def _build_param_pane(self, parent) -> None:
        pane = tk.Frame(parent, background=T.BG)
        parent.add(pane, weight=1)
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(2, weight=1)

        # ---- 固定的厂商选择条（不跟着参数区滚动） --------------------
        strip = tk.Frame(pane, background=T.PANEL, padx=T.px(14), pady=T.px(10))
        strip.grid(row=0, column=0, sticky="ew")
        strip.columnconfigure(1, weight=1)

        ttk.Label(strip, text="设备厂商：", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w")
        radios = tk.Frame(strip, background=T.PANEL)
        radios.grid(row=0, column=1, sticky="w", padx=(0, T.px(10)))
        for vendor in VENDORS:
            ttk.Radiobutton(radios, text=vendor.name, value=vendor.key,
                            variable=self.vendor_var, style="Card.TRadiobutton",
                            command=self._on_vendor_change).pack(side="left",
                                                                 padx=(0, T.px(12)))

        # 一键转换：把当前这套参数整套搬到另一家厂商。
        # 菜单内容在弹出前由 postcommand 现算，所以切过厂商之后目标列表自动就对。
        self.convert_menu = tk.Menu(strip, tearoff=0)
        self.convert_menu.configure(postcommand=self._refresh_convert_menu)
        self.convert_btn = ttk.Menubutton(strip, text="一键转换",
                                          style="Convert.TMenubutton",
                                          direction="below")
        self.convert_btn["menu"] = self.convert_menu
        self.convert_btn.grid(row=0, column=2, sticky="e", padx=(0, T.px(8)))

        ttk.Button(strip, text="生成全部场景", style="Gen.TButton",
                   command=self._gen_all).grid(row=0, column=3, sticky="e")

        self.strip_hint = ttk.Label(pane, text="", style="Faint.TLabel", anchor="w")
        self.strip_hint.grid(row=1, column=0, sticky="ew", padx=T.px(16),
                             pady=(T.px(5), T.px(6)))
        self.strip_hint.configure(
            text=f"{self._vendor().platform}　|　{self._vendor().port_hint}")

        # ---- 参数区（可滚动，一页到底） ------------------------------
        wrap = tk.Frame(pane, background=T.BG)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(wrap, background=T.BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=vbar.set)

        self.form = tk.Frame(self.canvas, background=T.BG)
        self._scroll_win = self.canvas.create_window((0, 0), window=self.form,
                                                     anchor="nw")
        self.form.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

        self.form.columnconfigure(0, weight=1)

        for index, scenario in enumerate(registry.SCENARIOS, 1):
            self._build_card(self.form, scenario, index)

        self._build_footer(self.form)
        self._bind_wheel(self.form)

    # ------------------------------------------------------------------
    # 参数区：一个个场景卡片
    # ------------------------------------------------------------------

    def _build_card(self, parent, scenario, index: int) -> None:
        # 外层 1px 边框 + 内层白底，做出卡片感
        outer = tk.Frame(parent, background=T.BORDER_SOFT)
        outer.pack(fill="x", padx=T.px(8), pady=(0, T.px(10)))
        self.card_frames[scenario.key] = outer
        card = tk.Frame(outer, background=T.PANEL)
        card.pack(fill="both", expand=True, padx=1, pady=1)

        # ---- 卡片头：序号 + 场景名 + 生成按钮 ------------------------
        head = tk.Frame(card, background=T.PANEL)
        head.pack(fill="x", padx=T.px(12), pady=(T.px(10), T.px(2)))
        head.columnconfigure(2, weight=1)

        ttk.Label(head, text=f"{index:02d}", style="CardNum.TLabel").grid(
            row=0, column=0, sticky="nw", padx=(0, T.px(8)))
        title = tk.Frame(head, background=T.PANEL)
        title.grid(row=0, column=1, sticky="w")
        ttk.Label(title, text=scenario.name, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(title, text=f"{scenario.group}　|　{scenario.summary}",
                  style="CardFaint.TLabel").pack(anchor="w", pady=(T.px(2), 0))

        btn = ttk.Button(head, text="生成脚本", style="Gen.TButton",
                         command=lambda k=scenario.key: self._gen_one(k))
        btn.grid(row=0, column=3, sticky="ne", padx=(T.px(12), 0))
        self.gen_buttons[scenario.key] = btn
        if not supports(self.vendor_key, scenario.key):
            btn.state(["disabled"])

        # ---- 参数网格：每格「标签 + 控件」同一行，两列 -----------------
        body = tk.Frame(card, background=T.PANEL)
        body.pack(fill="x", padx=T.px(10), pady=(T.px(6), T.px(10)))
        body.columnconfigure(0, weight=1, uniform="card")
        body.columnconfigure(1, weight=1, uniform="card")

        self.widgets[scenario.key] = {}
        self.choice_maps[scenario.key] = {}

        row, col = 0, 0
        for param in scenario.params:
            span = 2 if param.span >= 2 else 1
            if span == 2 and col == 1:
                row, col = row + 1, 0
            cell = tk.Frame(body, background=T.PANEL)
            cell.grid(row=row, column=col, columnspan=span, sticky="ew",
                      padx=T.px(4), pady=T.px(4))
            cell.columnconfigure(1, weight=1)
            self._build_param(cell, scenario, param, span)
            col += span
            if col >= 2:
                row, col = row + 1, 0

        # ---- 场景自带的提醒 -------------------------------------------
        for note in scenario.notes:
            lbl = ttk.Label(card, text=f"· {note}", style="CardFaint.TLabel",
                            justify="left")
            lbl.pack(anchor="w", padx=T.px(16), pady=(0, T.px(2)))
            self.help_labels.append((lbl, 2))
        if scenario.reboot:
            lbl = ttk.Label(card, text="· 注意：本场景会重启设备或中断业务，请在维护窗口内操作。",
                            style="CardWarn.TLabel", justify="left")
            lbl.pack(anchor="w", padx=T.px(16), pady=(0, T.px(2)))
            self.help_labels.append((lbl, 2))

        # 该卡片自己的状态行：未生成 / 已生成 / 出错
        state = ttk.Label(card, text="", style="CardFaint.TLabel", justify="left")
        state.pack(anchor="w", padx=T.px(16), pady=(T.px(2), T.px(9)))
        self.help_labels.append((state, 2))
        self.cards[scenario.key] = state

        tk.Frame(card, background=T.PANEL, height=T.px(2)).pack()

    def _build_param(self, cell, scenario, param, span: int) -> None:
        ttk.Label(cell, text=param.label, style="CardDim.TLabel").grid(
            row=0, column=0, sticky="nw", padx=(0, T.px(8)), pady=T.px(2))

        if param.kind == Kind.AREA:
            widget = tk.Text(cell, height=3, background=T.FIELD, foreground=T.FG,
                             insertbackground=T.FG, font=T.FONT_MONO, relief="flat",
                             bd=0, padx=T.px(7), pady=T.px(5), wrap="none",
                             highlightthickness=1, highlightbackground=T.BORDER,
                             highlightcolor=T.ACCENT,
                             selectbackground=T.SEL_BG, selectforeground=T.FG)
            widget.grid(row=0, column=1, sticky="ew", pady=T.px(1))
            widget.insert("1.0", str(param.default or ""))
            self.widgets[scenario.key][param.key] = ("text", widget)
        elif param.kind == Kind.CHOICE:
            labels = [lbl for _, lbl in param.choices]
            self.choice_maps[scenario.key][param.key] = {
                lbl: val for val, lbl in param.choices}
            combo = ttk.Combobox(cell, values=labels, state="readonly", font=T.FONT_UI)
            combo.grid(row=0, column=1, sticky="ew", pady=T.px(1))
            if labels:
                combo.set(labels[0])
            self.widgets[scenario.key][param.key] = ("choice", combo)
        elif param.kind == Kind.BOOL:
            var = tk.BooleanVar(value=str(param.default).strip().lower()
                                in ("1", "true", "yes", "on", "是"))
            ttk.Checkbutton(cell, text="启用", variable=var,
                            style="Card.TCheckbutton").grid(row=0, column=1,
                                                            sticky="w", pady=T.px(1))
            self.widgets[scenario.key][param.key] = ("bool", var)
        else:
            var = tk.StringVar(value=str(param.default or ""))
            ttk.Entry(cell, textvariable=var, font=T.FONT_UI).grid(
                row=0, column=1, sticky="ew", pady=T.px(1))
            self.widgets[scenario.key][param.key] = ("var", var)

        # 说明文字排在控件下面，跟着列宽折行
        parts = []
        if param.help:
            parts.append(param.help)
        if param.example:
            parts.append(f"例：{param.example}")
        if parts:
            lbl = ttk.Label(cell, text="　".join(parts), style="CardFaint.TLabel",
                            justify="left")
            lbl.grid(row=1, column=1, sticky="w", pady=(T.px(1), T.px(2)))
            self.help_labels.append((lbl, span))

    def _build_footer(self, parent) -> None:
        foot = tk.Frame(parent, background=T.BG)
        foot.pack(fill="x", padx=T.px(12), pady=(T.px(2), T.px(18)))
        ttk.Label(foot, text="说明：命令格式仅供参考！相同配置请核对端口名与版本后，"
                             "复制粘贴到设备即可。",
                  style="Faint.TLabel").pack(anchor="w")
        self.foot_meta = ttk.Label(foot, text="", style="Faint.TLabel")
        self.foot_meta.pack(anchor="w", pady=(T.px(6), 0))

    # ==================================================================
    # 页面二：设备命令详解 / 页面三、四：纯文本页
    # ==================================================================

    def _build_doc_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        bar = tk.Frame(page, background=T.BG)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, T.px(8)))
        ttk.Label(bar, text="查看", style="Dim.TLabel").pack(side="left")
        box = ttk.Combobox(bar, textvariable=self.doc_mode, values=list(DOC_MODES),
                           state="readonly", width=20, font=T.FONT_UI)
        box.pack(side="left", padx=(T.px(10), 0))
        box.bind("<<ComboboxSelected>>", lambda e: self._refresh_doc())
        ttk.Button(bar, text="复制详解", command=self._copy_doc).pack(side="right")
        self.doc_meta = ttk.Label(bar, text="", style="Faint.TLabel")
        self.doc_meta.pack(side="right", padx=(0, T.px(12)))

        self.doc = tk.Text(page, background=T.PANEL, foreground=T.FG,
                           insertbackground=T.FG, font=T.FONT_UI,
                           relief="flat", bd=0, wrap="word", padx=T.px(16),
                           pady=T.px(12), selectbackground=T.SEL_BG,
                           selectforeground=T.FG, spacing1=T.px(1), spacing3=T.px(1))
        self.doc.grid(row=1, column=0, sticky="nsew")
        dbar = ttk.Scrollbar(page, orient="vertical", command=self.doc.yview)
        dbar.grid(row=1, column=1, sticky="ns")
        self.doc.configure(yscrollcommand=dbar.set)

        self.doc.tag_configure("h1", foreground=T.C_HEAD,
                               font=("Microsoft YaHei UI", 11, "bold"),
                               spacing1=T.px(8), spacing3=T.px(5))
        self.doc.tag_configure("h2", foreground=T.C_BLOCK,
                               font=("Microsoft YaHei UI", 10, "bold"),
                               spacing1=T.px(7), spacing3=T.px(3))
        self.doc.tag_configure("title", foreground=T.FG,
                               font=("Microsoft YaHei UI", 10, "bold"),
                               spacing1=T.px(6))
        self.doc.tag_configure("syntax", foreground=T.C_HEAD, font=T.FONT_MONO)
        self.doc.tag_configure("body", foreground=T.FG)
        self.doc.tag_configure("dim", foreground=T.FG_FAINT)
        self.doc.tag_configure("caution", foreground=T.C_WARN)
        self.doc.tag_configure("version", foreground=T.C_NOTE)
        self.doc.tag_configure("compare", foreground=T.C_CMT, font=T.FONT_MONO)

    def _paint_mode_buttons(self) -> None:
        """按当前模式给标签按钮上色：选中的用强调色，未选中的用浅底。"""
        for mode, tab in self.mode_buttons.items():
            on = mode == self.script_mode
            tab.configure(background=T.ACCENT if on else T.TAB_IDLE,
                          foreground=T.ON_ACCENT if on else T.FG)

        # 基线入口只在回滚模式下有意义，其它模式收起来
        btn = getattr(self, "baseline_btn", None)
        if btn is None:
            return
        if self.script_mode == MODE_ROLLBACK:
            loaded = self.baseline is not None
            btn.configure(
                text=f"变更前配置 · 已载入 {len(self.baseline)} 条" if loaded
                     else "载入变更前配置",
                background=T.SEL_BG if loaded else T.TAB_IDLE,
                foreground=T.ACCENT if loaded else T.FG_DIM)
            btn.pack(side="right", padx=(T.px(6), 0))
        else:
            btn.pack_forget()

    def _set_script_mode(self, mode: str) -> None:
        """在「配置脚本」与「回滚脚本」之间切换。"""
        if mode == self.script_mode:
            return
        self.script_mode = mode
        self._paint_mode_buttons()
        self.script_title.configure(text=MODE_TITLES.get(mode, "▌ 一键配置脚本"))
        self._refresh_output(keep_scroll=False)
        if mode == MODE_ROLLBACK:
            self._flash("已切到回滚脚本：按逆序撤销当前脚本区的配置。"
                        "执行前先存档现网配置，别让回滚本身也需要回滚。")
        else:
            self._flash("已切回配置脚本")

    def _build_audit_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(page, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew")

        # ---- 左：粘贴区 ----------------------------------------------
        left = tk.Frame(paned, background=T.PANEL)
        paned.add(left, weight=1)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        head = tk.Frame(left, background=T.ACCENT)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text="▌ 粘贴设备配置", style="ScriptTab.TLabel").pack(side="left")
        self.audit_meta = ttk.Label(head, text="", style="ScriptTab.TLabel")
        self.audit_meta.pack(side="right", padx=(0, T.px(10)))

        self.audit_in = tk.Text(left, background=T.PANEL, foreground=T.C_CMD,
                                insertbackground=T.FG, font=T.FONT_MONO,
                                relief="flat", bd=0, wrap="none",
                                padx=T.px(12), pady=T.px(10),
                                selectbackground=T.SEL_BG, selectforeground=T.FG)
        self.audit_in.grid(row=1, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(left, orient="vertical", command=self.audit_in.yview)
        vbar.grid(row=1, column=1, sticky="ns")
        hbar = ttk.Scrollbar(left, orient="horizontal", command=self.audit_in.xview)
        hbar.grid(row=2, column=0, sticky="ew")
        self.audit_in.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        foot = tk.Frame(left, background=T.PANEL)
        foot.grid(row=3, column=0, columnspan=2, sticky="ew",
                  padx=T.px(10), pady=(T.px(6), T.px(10)))
        ttk.Button(foot, text="开始体检", style="Accent.TButton",
                   command=self._run_audit).pack(side="left")
        ttk.Button(foot, text="清空", style="Ghost.TButton",
                   command=self._clear_audit).pack(side="left", padx=T.px(6))

        # AI 补充是第三层，默认不勾。前两层已经能查掉大部分，联网应该是
        # 用户主动选的动作，不该悄悄发生
        self.audit_ai_var = tk.BooleanVar(value=False)
        self.audit_ai_chk = ttk.Checkbutton(
            foot, text="AI 补充语义层", variable=self.audit_ai_var,
            command=self._on_audit_ai_toggle)
        self.audit_ai_chk.pack(side="left", padx=(T.px(10), 0))

        self.audit_hint = ttk.Label(foot, text="只解析这段文本，不连设备也不上传",
                                    style="Faint.TLabel")
        self.audit_hint.pack(side="right")

        # ---- 右：报告区 ----------------------------------------------
        right = tk.Frame(paned, background=T.PANEL)
        paned.add(right, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        rhead = tk.Frame(right, background=T.ACCENT)
        rhead.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(rhead, text="▌ 体检报告", style="ScriptTab.TLabel").pack(side="left")
        self.audit_score = ttk.Label(rhead, text="", style="ScriptTab.TLabel")
        self.audit_score.pack(side="right", padx=(0, T.px(10)))

        self.audit_out = tk.Text(right, background=T.PANEL, foreground=T.FG,
                                 insertbackground=T.FG, font=T.FONT_UI,
                                 relief="flat", bd=0, wrap="word",
                                 padx=T.px(16), pady=T.px(12),
                                 selectbackground=T.SEL_BG, selectforeground=T.FG,
                                 spacing1=T.px(1), spacing3=T.px(1))
        self.audit_out.grid(row=1, column=0, sticky="nsew")
        rbar = ttk.Scrollbar(right, orient="vertical", command=self.audit_out.yview)
        rbar.grid(row=1, column=1, sticky="ns")
        self.audit_out.configure(yscrollcommand=rbar.set, state="disabled")

        self.audit_out.tag_configure("h1", foreground=T.C_HEAD,
                                     font=("Microsoft YaHei UI", 11, "bold"),
                                     spacing1=T.px(9), spacing3=T.px(5))
        self.audit_out.tag_configure("title", foreground=T.FG,
                                     font=("Microsoft YaHei UI", 10, "bold"),
                                     spacing1=T.px(5))
        self.audit_out.tag_configure("body", foreground=T.FG)
        self.audit_out.tag_configure("dim", foreground=T.FG_FAINT)
        self.audit_out.tag_configure("warn", foreground=T.C_WARN)
        self.audit_out.tag_configure("bad", foreground=T.C_ERR)
        self.audit_out.tag_configure("ok", foreground=T.OK)
        self.audit_out.tag_configure("syntax", foreground=T.C_HEAD, font=T.FONT_MONO)

        rfoot = tk.Frame(right, background=T.PANEL)
        rfoot.grid(row=2, column=0, columnspan=2, sticky="ew",
                   padx=T.px(10), pady=(T.px(6), T.px(10)))
        ttk.Button(rfoot, text="复制报告", style="Ghost.TButton",
                   command=self._copy_audit).pack(side="left")
        self._last_audit = None
        if getattr(self, "audit_source", ""):
            # 切主题会整页重建，把用户粘过的内容放回去，别让人重粘一遍
            self.audit_in.insert("1.0", self.audit_source)
        self._refresh_audit()

    def _run_audit(self) -> None:
        """三层检查：安全体检 → 语法校对 → 在线语义补充。

        前两层都是离线的、毫秒级的，所以先跑完再把报告画出来；
        第三层要联网，放到后台线程，出来什么补什么。
        """
        self.audit_source = self.audit_in.get("1.0", "end").rstrip("\n")
        if not self.audit_source.strip():
            self._flash("还没有内容。把设备的配置整段粘到左边，再点「开始体检」")
            return

        report = audit.analyze(self.audit_source)
        self._last_audit = report
        self._ai_result = None

        # 第二层：语法与结构校对
        self._syntax = self._run_syntax(report)
        self._refresh_audit()

        syntax_findings = (self._syntax or ([], []))[0]
        self._flash(f"体检完成：健康分 {report.score}/100，"
                    f"高危 {len(report.high)} 项、中危 {len(report.mid)} 项，"
                    f"语法问题 {len(syntax_findings)} 处")

        # 第三层：在线语义补充（可选）
        if self.audit_ai_var.get():
            self._run_audit_ai()

    def _run_syntax(self, report):
        """语法校对。按体检检测出的厂商走，比用户在主页选的那个更准。"""
        try:
            from ..ai import rules
        except Exception:  # noqa: BLE001 —— AI 包缺失不该影响体检
            return None

        vendor = getattr(report, "vendor_key", "") or self.vendor_key
        if not vendor:
            return None
        try:
            return rules.check(vendor, self.audit_source or "")
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # 第三层：在线语义补充
    # ------------------------------------------------------------------

    def _on_audit_ai_toggle(self) -> None:
        if self.audit_ai_var.get():
            self.audit_hint.configure(text="勾选后会把脱敏过的文本发给模型")
        else:
            self.audit_hint.configure(text="只解析这段文本，不连设备也不上传")

    def _run_audit_ai(self) -> None:
        if self._audit_busy:
            return

        try:
            from ..ai import advise
            from ..ai import config as aicfg
        except Exception as exc:  # noqa: BLE001
            self._set_ai_error(f"AI 模块不可用：{exc}")
            return

        cfg = aicfg.load()
        if not cfg.ready():
            self._set_ai_error("还没配置接口和密钥，去「AI 助手」页右上角设置")
            return

        known = advise.known_titles(self._last_audit,
                                    (self._syntax or ([], []))[0])
        vendor = getattr(self._last_audit, "vendor_key", "") or self.vendor_key
        text = self.audit_source

        self._audit_busy = True
        self._ai_result = None
        self.audit_hint.configure(text="正在做语义分析…")
        self._refresh_audit()

        def run():
            try:
                data = advise.review(text, vendor, known, cfg)
            except Exception as exc:  # noqa: BLE001 —— worker 里任何异常都要回主线程
                data = {"findings": [], "note": "", "redacted": 0,
                        "error": f"在线分析失败：{exc}"}
            self._audit_queue.put(data)

        threading.Thread(target=run, daemon=True).start()
        self.after(120, self._audit_ai_poll)

    def _set_ai_error(self, message: str) -> None:
        self._ai_result = {"findings": [], "note": "", "redacted": 0,
                           "error": message}
        self._refresh_audit()

    def _audit_ai_poll(self) -> None:
        # 切主题会把这一页整个重建，旧的轮询得自己退场
        try:
            if not self.audit_out.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            data = self._audit_queue.get_nowait()
        except queue.Empty:
            if self._audit_busy:
                self.after(120, self._audit_ai_poll)
            return

        self._audit_busy = False
        self._ai_result = data
        self._on_audit_ai_toggle()
        self._refresh_audit()

        if data.get("error"):
            self._flash(f"AI 补充没跑成：{data['error']}")
        else:
            count = len(data.get("findings") or [])
            self._flash(f"AI 补充完成，新增 {count} 条")

    def _clear_audit(self) -> None:
        self.audit_in.delete("1.0", "end")
        self.audit_source = ""
        self._last_audit = None
        self._syntax = None
        self._ai_result = None
        self._refresh_audit()
        self._flash("已清空体检内容")

    def _refresh_audit(self) -> None:
        if not hasattr(self, "audit_out"):
            return
        report = getattr(self, "_last_audit", None)
        if report is None and getattr(self, "audit_source", ""):
            report = audit.analyze(self.audit_source)
            self._last_audit = report
        lines = audit.render(report) if report else audit.render(audit.Report())

        self.audit_out.configure(state="normal")
        self.audit_out.delete("1.0", "end")
        for line in lines:
            self.audit_out.insert("end", line + "\n", self._audit_tag(line))
        self._append_syntax()
        self._append_ai()
        self.audit_out.configure(state="disabled")

        if report and report.findings:
            self.audit_score.configure(text=f"健康分 {report.score}")
        else:
            self.audit_score.configure(text="")

        source_lines = len(self.audit_source.splitlines()) if getattr(
            self, "audit_source", "") else 0
        self.audit_meta.configure(
            text=f"{source_lines} 行" if source_lines else "尚未粘贴")

    def _append_syntax(self) -> None:
        """把语法校对结果接在体检报告后面。第二层。"""
        syntax = self._syntax
        if not syntax:
            return
        findings, unmatched = syntax
        out = self.audit_out

        out.insert("end", "\n")
        out.insert("end", "━━ 语法与结构校对 ━━\n", "h1")

        if not findings and not unmatched:
            out.insert("end", "  命令都落在该落的视图里，没发现语法问题。\n", "ok")
            return

        labels = {"error": "错误", "warn": "警告", "info": "提示"}
        tags = {"error": "bad", "warn": "warn", "info": "dim"}
        for item in findings:
            where = f"第 {item.line} 行" if item.line else "整段"
            out.insert("end",
                       f"  [{labels.get(item.level, '提示')}] {where}　{item.message}\n",
                       tags.get(item.level, "body"))
            if item.text:
                out.insert("end", f"      {item.text}\n", "syntax")
            if item.evidence:
                out.insert("end", f"      依据：{item.evidence}\n", "dim")
            if item.fix:
                out.insert("end", f"      建议：{item.fix}\n", "dim")

        if unmatched:
            out.insert("end",
                       f"\n  {len(unmatched)} 行不在已知语法库内"
                       f"（可能是版本差异，也可能拼错了）：\n", "dim")
            for item in unmatched:
                out.insert("end", f"      第 {item['line']} 行  {item['text']}\n",
                           "syntax")
                if item.get("suggest"):
                    out.insert("end", f"          最接近：{item['suggest']}\n", "dim")

    def _append_ai(self) -> None:
        """把在线语义补充接在报告最后。第三层。"""
        data = self._ai_result
        if data is None:
            return
        out = self.audit_out

        out.insert("end", "\n")
        out.insert("end", "━━ AI 补充（语义层） ━━\n", "h1")

        if data.get("error"):
            out.insert("end", f"  没跑成：{data['error']}\n", "dim")
            return

        if data.get("redacted"):
            out.insert("end", f"  已脱敏 {data['redacted']} 处，原文只在本机\n", "dim")

        findings = data.get("findings") or []
        if not findings:
            out.insert("end", "  前两层之外没有发现新问题。\n", "ok")

        labels = {"high": "高危", "mid": "中危", "low": "建议"}
        tags = {"high": "bad", "mid": "warn", "low": "dim"}
        for item in findings:
            where = f"第 {item['line']} 行" if item.get("line") else "整段"
            out.insert("end",
                       f"  [{labels.get(item['level'], '建议')}] {where}　"
                       f"{item['message']}\n", tags.get(item["level"], "body"))
            if item.get("evidence"):
                out.insert("end", f"      依据：{item['evidence']}\n", "dim")
            if item.get("fix"):
                out.insert("end", f"      建议：{item['fix']}\n", "dim")

        if data.get("note"):
            out.insert("end", f"\n  {data['note']}\n", "dim")

    @staticmethod
    def _audit_tag(line: str) -> str:
        if line.startswith("━━"):
            return "h1"
        if line.startswith("  ✓"):
            return "ok"
        if line.startswith("健康分") or line.startswith("识别为"):
            return "title"
        if re.match(r"^\d+\. ", line):
            return "title"
        if line.startswith("   证据："):
            return "dim"
        if line.startswith("   修复："):
            return "warn"
        if line.startswith("     ") and line.strip():
            return "syntax"
        if line.startswith("   "):
            return "dim"
        return "body"

    def _copy_audit(self) -> None:
        text = self.audit_out.get("1.0", "end").rstrip("\n")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._flash("体检报告已复制，可直接贴进交付文档")

    def _diff_input(self, parent, title: str, column: int):
        """左右各一个输入框：上面标题条、中间正文、下面行数。"""
        box = tk.Frame(parent, background=T.PANEL)
        box.grid(row=0, column=column, sticky="nsew",
                 padx=T.px(4), pady=T.px(2))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        head = tk.Frame(box, background=T.ACCENT)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=f"▌ {title}", style="ScriptTab.TLabel").pack(side="left")
        meta = ttk.Label(head, text="空", style="ScriptTab.TLabel")
        meta.pack(side="right", padx=(0, T.px(10)))

        text = tk.Text(box, background=T.PANEL, foreground=T.C_CMD,
                       insertbackground=T.FG, font=T.FONT_MONO,
                       relief="flat", bd=0, wrap="none",
                       padx=T.px(10), pady=T.px(8),
                       selectbackground=T.SEL_BG, selectforeground=T.FG)
        text.grid(row=1, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        vbar.grid(row=1, column=1, sticky="ns")
        hbar = ttk.Scrollbar(box, orient="horizontal", command=text.xview)
        hbar.grid(row=2, column=0, sticky="ew")
        text.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        return text, meta

    def _build_diff_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        # 输入区给两成七、报告区给三成 —— 差异条目比原始配置更值得占屏幕
        page.rowconfigure(0, weight=33, uniform="v")
        page.rowconfigure(1, weight=67, uniform="v")

        # ---- 上半：两侧输入 ------------------------------------------
        top = tk.Frame(page, background=T.BG)
        top.grid(row=0, column=0, sticky="nsew", pady=(T.px(2), 0))
        top.columnconfigure(0, weight=1, uniform="side")
        top.columnconfigure(1, weight=1, uniform="side")
        top.rowconfigure(0, weight=1)

        self.diff_a, self.diff_a_meta = self._diff_input(top, "A 侧配置", 0)
        self.diff_b, self.diff_b_meta = self._diff_input(top, "B 侧配置", 1)

        bar = tk.Frame(top, background=T.BG)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, T.px(4)))
        ttk.Button(bar, text="开始对比", style="Accent.TButton",
                   command=self._run_diff).pack(side="left", padx=(T.px(6), 0))
        ttk.Button(bar, text="清空", style="Ghost.TButton",
                   command=self._clear_diff).pack(side="left", padx=T.px(6))
        ttk.Button(bar, text="A 侧填入当前脚本区", style="Ghost.TButton",
                   command=self._diff_from_script).pack(side="left", padx=(0, T.px(6)))
        ttk.Label(bar, text="两侧都粘上去再点对比；顺序不同不算差异",
                  style="Faint.TLabel").pack(side="left", padx=T.px(6))

        # ---- 下半：报告 ----------------------------------------------
        bottom = tk.Frame(page, background=T.PANEL)
        bottom.grid(row=1, column=0, sticky="nsew", pady=(T.px(4), 0))
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)

        rhead = tk.Frame(bottom, background=T.ACCENT)
        rhead.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(rhead, text="▌ 差异报告", style="ScriptTab.TLabel").pack(side="left")
        self.diff_score = ttk.Label(rhead, text="", style="ScriptTab.TLabel")
        self.diff_score.pack(side="right", padx=(0, T.px(10)))
        ttk.Button(rhead, text="复制报告", style="Ghost.TButton",
                   command=self._copy_diff).pack(side="right", padx=(0, T.px(8)))

        self.diff_out = tk.Text(bottom, background=T.PANEL, foreground=T.FG,
                                insertbackground=T.FG, font=T.FONT_UI,
                                relief="flat", bd=0, wrap="word",
                                padx=T.px(16), pady=T.px(12),
                                selectbackground=T.SEL_BG, selectforeground=T.FG,
                                spacing1=T.px(1), spacing3=T.px(1))
        self.diff_out.grid(row=1, column=0, sticky="nsew")
        rbar = ttk.Scrollbar(bottom, orient="vertical", command=self.diff_out.yview)
        rbar.grid(row=1, column=1, sticky="ns")
        self.diff_out.configure(yscrollcommand=rbar.set, state="disabled")

        self.diff_out.tag_configure("h1", foreground=T.C_HEAD,
                                    font=("Microsoft YaHei UI", 11, "bold"),
                                    spacing1=T.px(8), spacing3=T.px(4))
        self.diff_out.tag_configure("title", foreground=T.FG,
                                    font=("Microsoft YaHei UI", 10, "bold"),
                                    spacing1=T.px(4))
        self.diff_out.tag_configure("body", foreground=T.FG)
        self.diff_out.tag_configure("dim", foreground=T.FG_FAINT)
        self.diff_out.tag_configure("warn", foreground=T.C_WARN)
        self.diff_out.tag_configure("ok", foreground=T.OK)
        self.diff_out.tag_configure("syntax", foreground=T.C_HEAD, font=T.FONT_MONO)

        self._last_diff = None
        if self.diff_a_source:
            self.diff_a.insert("1.0", self.diff_a_source)
        if self.diff_b_source:
            self.diff_b.insert("1.0", self.diff_b_source)
        self._refresh_diff()

    def _diff_from_script(self) -> None:
        """把当前脚本区生成的配置填进 A 侧，方便和现网配置比。"""
        text = self._script_text()
        if not text:
            self._flash("脚本区还是空的，先在「配置脚本」里生成一段")
            return
        self.diff_a.delete("1.0", "end")
        self.diff_a.insert("1.0", text)
        self.diff_a_source = text
        self._refresh_diff()
        self._flash("已把当前脚本区的配置填进 A 侧，B 侧粘贴设备上现有的配置再对比")

    def _run_diff(self) -> None:
        self.diff_a_source = self.diff_a.get("1.0", "end").rstrip("\n")
        self.diff_b_source = self.diff_b.get("1.0", "end").rstrip("\n")
        report = diffcfg.compare(self.diff_a_source, self.diff_b_source,
                                 "A 侧", "B 侧")
        self._last_diff = report
        self._refresh_diff()
        if not report.items and not (report.a_lines and report.b_lines):
            self._flash("两侧都粘上配置再点对比")
        elif not report.items:
            self._flash("两份配置逐项一致，没有发现差异")
        else:
            self._flash(f"对比完成：一致度 {report.consistency * 100:.1f}%，"
                        f"差异 {report.total} 项")

    def _clear_diff(self) -> None:
        for widget in (self.diff_a, self.diff_b):
            widget.delete("1.0", "end")
        self.diff_a_source = ""
        self.diff_b_source = ""
        self._last_diff = None
        self._refresh_diff()
        self._flash("已清空对比内容")

    def _refresh_diff(self) -> None:
        if not hasattr(self, "diff_out"):
            return
        report = getattr(self, "_last_diff", None)
        if report is None and (self.diff_a_source or self.diff_b_source):
            report = diffcfg.compare(self.diff_a_source, self.diff_b_source,
                                     "A 侧", "B 侧")
            self._last_diff = report
        lines = diffcfg.render(report) if report else diffcfg.render(diffcfg.DiffReport())

        self.diff_out.configure(state="normal")
        self.diff_out.delete("1.0", "end")
        for line in lines:
            self.diff_out.insert("end", line + "\n", self._diff_tag(line))
        self.diff_out.configure(state="disabled")

        for widget, meta, source in (
                (self.diff_a, self.diff_a_meta, self.diff_a_source),
                (self.diff_b, self.diff_b_meta, self.diff_b_source)):
            count = len(source.splitlines()) if source else 0
            meta.configure(text=f"{count} 行" if count else "空")

        if report and report.items:
            self.diff_score.configure(text=f"一致度 {report.consistency * 100:.1f}%")
        elif report and report.a_lines and report.b_lines:
            self.diff_score.configure(text="一致度 100%")
        else:
            self.diff_score.configure(text="")

    @staticmethod
    def _diff_tag(line: str) -> str:
        if line.startswith("━━"):
            return "h1"
        if line.startswith("  ✓"):
            return "ok"
        if line.startswith("· "):
            return "title"
        if line.startswith("    A：") or line.startswith("    B："):
            return "syntax"
        if line.startswith("    只有") or line.startswith("　"):
            return "dim"
        if line.startswith("一致度"):
            return "title"
        return "body"

    def _copy_diff(self) -> None:
        text = self.diff_out.get("1.0", "end").rstrip("\n")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._flash("差异报告已复制")

    def _build_diag_page(self, page) -> None:
        page.columnconfigure(0, minsize=T.px(210))
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)

        # ---- 左：现象列表 --------------------------------------------
        left = tk.Frame(page, background=T.PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, T.px(8)))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        head = tk.Frame(left, background=T.ACCENT)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="▌ 现象", style="ScriptTab.TLabel").pack(side="left")

        self.diag_list = tk.Listbox(
            left, background=T.PANEL, foreground=T.FG,
            selectbackground=T.SEL_BG, selectforeground=T.FG,
            font=T.FONT_UI, relief="flat", bd=0, highlightthickness=0,
            activestyle="none", exportselection=False)
        self.diag_list.grid(row=1, column=0, sticky="nsew", pady=T.px(6))
        for case in diagnose.CASES:
            self.diag_list.insert("end", "  " + case.name)
        self.diag_list.bind("<<ListboxSelect>>", lambda e: self._show_case())

        tip = ttk.Label(left, text="选一个现象看排查步骤",
                        style="Faint.TLabel", wraplength=T.px(190),
                        justify="left")
        tip.grid(row=2, column=0, sticky="w", padx=T.px(4), pady=(0, T.px(8)))

        # ---- 右：排查步骤 --------------------------------------------
        right = tk.Frame(page, background=T.PANEL)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        rhead = tk.Frame(right, background=T.ACCENT)
        rhead.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(rhead, text="▌ 排查步骤", style="ScriptTab.TLabel").pack(side="left")
        self.diag_meta = ttk.Label(rhead, text="", style="ScriptTab.TLabel")
        self.diag_meta.pack(side="right", padx=(0, T.px(10)))
        ttk.Button(rhead, text="复制步骤", style="Ghost.TButton",
                   command=self._copy_diag).pack(side="right", padx=(0, T.px(8)))

        self.diag_out = tk.Text(right, background=T.PANEL, foreground=T.FG,
                                insertbackground=T.FG, font=T.FONT_UI,
                                relief="flat", bd=0, wrap="word",
                                padx=T.px(16), pady=T.px(12),
                                selectbackground=T.SEL_BG, selectforeground=T.FG,
                                spacing1=T.px(1), spacing3=T.px(1))
        self.diag_out.grid(row=1, column=0, sticky="nsew")
        dbar = ttk.Scrollbar(right, orient="vertical", command=self.diag_out.yview)
        dbar.grid(row=1, column=1, sticky="ns")
        self.diag_out.configure(yscrollcommand=dbar.set, state="disabled")

        self.diag_out.tag_configure("h1", foreground=T.C_HEAD,
                                    font=("Microsoft YaHei UI", 11, "bold"),
                                    spacing1=T.px(9), spacing3=T.px(5))
        self.diag_out.tag_configure("step", foreground=T.FG,
                                    font=("Microsoft YaHei UI", 10, "bold"),
                                    spacing1=T.px(7))
        self.diag_out.tag_configure("body", foreground=T.FG)
        self.diag_out.tag_configure("dim", foreground=T.FG_FAINT)
        self.diag_out.tag_configure("warn", foreground=T.C_WARN)
        self.diag_out.tag_configure("syntax", foreground=T.C_HEAD, font=T.FONT_MONO)

        self.diag_list.selection_set(0)
        self._show_case()

    def _current_case(self):
        selection = self.diag_list.curselection()
        index = selection[0] if selection else 0
        if index >= len(diagnose.CASES):
            index = 0
        return diagnose.CASES[index]

    def _show_case(self) -> None:
        case = self._current_case()
        index = self.diag_list.curselection()
        index = index[0] if index else 0
        for item in range(self.diag_list.size()):
            self.diag_list.itemconfig(
                item,
                background=T.SEL_BG if item == index else T.PANEL,
                foreground=T.FG)
        lines = diagnose.render(case, self.vendor_key)

        self.diag_out.configure(state="normal")
        self.diag_out.delete("1.0", "end")
        for line in lines:
            self.diag_out.insert("end", line + "\n", self._diag_tag(line))
        self.diag_out.configure(state="disabled")

        family = "Comware 系命令" if self.vendor_key in ("h3c", "huawei") else "IOS 系命令"
        self.diag_meta.configure(text=f"{len(case.steps)} 步　·　{family}")

    @staticmethod
    def _diag_tag(line: str) -> str:
        if line.startswith("━━"):
            return "h1"
        if re.match(r"^\d+\. ", line):
            return "step"
        if line.startswith("      ") or line.startswith("     "):
            return "syntax"
        if line.startswith("   "):
            return "dim"
        if line.startswith("现象："):
            return "body"
        return "body"

    def _copy_diag(self) -> None:
        text = self.diag_out.get("1.0", "end").rstrip("\n")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._flash("排查步骤已复制")

    def _current_text(self) -> str:
        """脚本区当前该显示什么 —— 取决于顶部的模式选择。"""
        if self.script_mode == MODE_ROLLBACK:
            return self._rollback_text()
        if self.script_mode == MODE_BLUEPRINT:
            return self._blueprint_text()
        return self._script_text()

    def _blueprint_text(self) -> str:
        """把已生成的场景按开局依赖顺序编排成整机脚本。"""
        keys = self._active_keys()
        if not keys:
            self._last_blueprint = None
            return ""

        results = {key: self.results[key] for key in keys}
        plan = blueprint.build(self.vendor_key, results,
                               vendor_name=self._vendor().name)
        self._last_blueprint = plan
        info = blueprint.coverage(results)

        cc = self._comment_char()
        vendor = self._vendor()
        width = 68
        out = [
            cc + "=" * width,
            f"{cc} {vendor.name} · 整机开局脚本",
            f"{cc} 按开局依赖顺序编排：{info['stages']} 个阶段 / {info['scenes']} 个场景",
            f"{cc} 段与段可顺序整段粘贴；保存只在最后执行一次",
            cc + "=" * width,
        ]

        # 整机脚本始终保留阶段标题 —— 那是结构，去掉就分不清哪段配什么了。
        # 「保留注释」开关在这里只控制说明性注释与命令行里的注释。
        keep_comment = self.show_comment.get()
        for block in plan.blocks:
            if block.title:
                out.append("")
                out.append(f"{cc} ---- {block.title} ----")
            if block.comment and keep_comment:
                for line in block.comment.splitlines():
                    out.append(f"{cc} {line}")
            for line in block.lines:
                if not keep_comment and str(line).lstrip()[:1] in ("#", "!"):
                    continue
                out.append(line)

        if plan.warnings:
            out.append("")
            for item in plan.warnings:
                out.append(f"{cc} [风险] {item}")
        if plan.notes:
            out.append("")
            for item in plan.notes:
                out.append(f"{cc} [提示] {item}")
        return "\n".join(out)

    def _comment_char(self) -> str:
        """注释符必须跟着厂商走：思科 / 锐捷的 # 不是注释，会被当命令执行。"""
        return "!" if self.vendor_key in ("cisco", "ruijie") else "#"

    def _open_baseline(self) -> None:
        """载入变更前的配置，把回滚从「撤销」升级成「恢复原值」。"""
        win = tk.Toplevel(self)
        self._baseline_win = win          # 留个引用，截图自检要用
        win.title("变更前的配置 · 用于精确回滚")
        win.configure(background=T.BG)
        win.transient(self)
        win.grab_set()

        width, height = T.px(760), T.px(600)
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 3)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(T.px(520), T.px(420))

        head = tk.Frame(win, background=T.ACCENT)
        head.pack(fill="x")
        ttk.Label(head, text="▌ 变更前的配置", style="ScriptTab.TLabel").pack(side="left")

        tip = ttk.Label(
            win, style="Dim.TLabel", justify="left", wraplength=width - T.px(40),
            text=("把变更前这个接口（或整机）的配置粘进来，回滚会用里面的原值。\n\n"
                  "  H3C / 华为    display current-configuration interface GigabitEthernet1/0/1\n"
                  "  思科 / 锐捷   show running-config interface GigabitEthernet1/0/1\n\n"
                  "不粘也能回滚，只是那些「撤销后会回到默认值」的命令只能给 undo；\n"
                  "粘了之后，它们会改成恢复成你原来的取值。"))
        tip.pack(fill="x", padx=T.px(16), pady=(T.px(12), T.px(8)))

        wrap = tk.Frame(win, background=T.PANEL)
        wrap.pack(fill="both", expand=True, padx=T.px(16))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        box = tk.Text(wrap, background=T.PANEL, foreground=T.C_CMD,
                      insertbackground=T.FG, font=T.FONT_MONO,
                      relief="flat", bd=0, wrap="none",
                      padx=T.px(10), pady=T.px(8),
                      selectbackground=T.SEL_BG, selectforeground=T.FG)
        box.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=box.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        box.configure(yscrollcommand=vbar.set)
        if self.baseline_source:
            box.insert("1.0", self.baseline_source)
        box.focus_set()

        foot = tk.Frame(win, background=T.BG)
        foot.pack(fill="x", padx=T.px(16), pady=T.px(12))

        def apply_and_close():
            text = box.get("1.0", "end").strip()
            if not text:
                self.baseline = None
                self.baseline_source = ""
                self._paint_mode_buttons()
                self._refresh_output(keep_scroll=False)
                self._flash("已清除变更前配置，回滚恢复为普通撤销")
                win.destroy()
                return

            baseline = rollback.Baseline.parse(text, self.vendor_key)
            self.baseline = baseline
            self.baseline_source = text
            self._paint_mode_buttons()
            self._refresh_output(keep_scroll=False)
            win.destroy()
            self._flash(f"已载入变更前配置：{len(baseline)} 条，"
                        f"{baseline.contexts} 个视图")

        ttk.Button(foot, text="应用", style="Accent.TButton",
                   command=apply_and_close).pack(side="right")
        ttk.Button(foot, text="取消", style="Ghost.TButton",
                   command=win.destroy).pack(side="right", padx=T.px(6))
        ttk.Button(foot, text="清除已载入的", style="Ghost.TButton",
                   command=lambda: box.delete("1.0", "end")).pack(side="left")
        win.bind("<Escape>", lambda e: win.destroy())

    def _rollback_text(self) -> str:
        """把当前脚本区的配置反推成撤销脚本。"""
        keys = self._active_keys()
        if not keys:
            self._last_rollback = None
            return ""

        results = [self.results[key] for key in keys]
        undo = rollback.build(
            self.vendor_key, results,
            scenario_name=f"脚本区当前 {len(keys)} 个场景",
            vendor_name=self._vendor().name,
            baseline=self.baseline)
        self._last_rollback = undo

        cc = self._comment_char()
        vendor = self._vendor()
        width = 68
        out = [
            cc + "=" * width,
            f"{cc} {vendor.name} · 回滚脚本（撤销脚本区当前的 {len(keys)} 个场景）",
            f"{cc} {APP_NAME} · 按配置的逆序排列，后配的先撤",
            (f"{cc} 已载入变更前配置（{len(self.baseline)} 条），覆盖型命令按原值恢复"
             if self.baseline is not None else
             f"{cc} 未载入变更前配置：只能撤销到默认值。点右上角「载入变更前配置」可精确回滚"),
            f"{cc} 执行前请先存档现网配置：display current-configuration / show running-config",
            cc + "=" * width,
        ]

        body = undo.to_text(header=False, comments=self.show_comment.get())
        if body.strip():
            out.append("")
            out.append(body)

        if undo.warnings:
            out.append("")
            for item in undo.warnings:
                out.append(f"{cc} [风险] {item}")
        if undo.notes:
            out.append("")
            for item in undo.notes:
                out.append(f"{cc} [提示] {item}")
        return "\n".join(out)

    def _build_text_page(self, page, lines) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        text = tk.Text(page, background=T.PANEL, foreground=T.FG,
                       insertbackground=T.FG, font=T.FONT_UI,
                       relief="flat", bd=0, wrap="word", padx=T.px(18),
                       pady=T.px(14), selectbackground=T.SEL_BG,
                       selectforeground=T.FG, spacing1=T.px(2), spacing3=T.px(2))
        text.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(page, orient="vertical", command=text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=bar.set)

        text.tag_configure("h1", foreground=T.C_HEAD,
                           font=("Microsoft YaHei UI", 11, "bold"),
                           spacing1=T.px(9), spacing3=T.px(5))
        text.tag_configure("h2", foreground=T.C_BLOCK,
                           font=("Microsoft YaHei UI", 10, "bold"),
                           spacing1=T.px(7), spacing3=T.px(3))
        text.tag_configure("body", foreground=T.FG)
        text.tag_configure("dim", foreground=T.FG_FAINT)
        text.tag_configure("ok", foreground=T.OK)
        text.tag_configure("warn", foreground=T.C_WARN)
        text.tag_configure("syntax", foreground=T.C_HEAD, font=T.FONT_MONO)

        for line in lines:
            text.insert("end", line + "\n", self._doc_tag(line))
        text.configure(state="disabled")
        if page is self.page_verify:
            self.verify_text = text
        if page is self.page_help:
            self.help_text = text

    # ==================================================================
    # 取值 / 回填
    # ==================================================================

    def _vendor(self):
        return next(v for v in VENDORS if v.key == self.vendor_key)

    def _save_fields(self) -> None:
        for scenario in registry.SCENARIOS:
            self.store[(self.vendor_key, scenario.key)] = self._collect(scenario)

    def _collect(self, scenario) -> dict:
        data: dict = {}
        built = self.widgets.get(scenario.key, {})
        for param in scenario.params:
            entry = built.get(param.key)
            if entry is None:
                data[param.key] = param.default
                continue
            kind, widget = entry
            if kind == "text":
                data[param.key] = widget.get("1.0", "end").rstrip("\n")
            elif kind == "bool":
                data[param.key] = "1" if widget.get() else ""
            elif kind == "choice":
                label = widget.get()
                data[param.key] = self.choice_maps[scenario.key][param.key].get(
                    label, label)
            else:
                data[param.key] = widget.get()
        return data

    def _restore_fields(self) -> None:
        """把当前厂商保存过的参数回填到界面上。"""
        for scenario in registry.SCENARIOS:
            values = self.store.get((self.vendor_key, scenario.key))
            if values is None:
                values = {p.key: p.default for p in scenario.params}
            built = self.widgets.get(scenario.key, {})
            for param in scenario.params:
                entry = built.get(param.key)
                if entry is None:
                    continue
                kind, widget = entry
                value = values.get(param.key, param.default)
                if kind == "text":
                    widget.delete("1.0", "end")
                    widget.insert("1.0", str(value or ""))
                elif kind == "bool":
                    widget.set(str(value).strip().lower()
                               in ("1", "true", "yes", "on", "是"))
                elif kind == "choice":
                    mapping = self.choice_maps[scenario.key][param.key]
                    label = next((l for l, v in mapping.items()
                                  if v == str(value)), None)
                    if label is None:
                        label = next(iter(mapping), "")
                    widget.set(label)
                else:
                    widget.set(str(value or ""))

    # ==================================================================
    # 生成
    # ==================================================================

    def _gen_one(self, scenario_key: str) -> None:
        ok = self._generate(scenario_key)
        self._mark_dirty()
        self._refresh_output(keep_scroll=False)
        self._refresh_doc()
        scenario = registry.get(scenario_key)
        if ok:
            self._flash(f"已生成【{scenario.name}】的命令，见左侧脚本区")
        else:
            self._flash(f"【{scenario.name}】生成失败：{self.errors.get(scenario_key, '')}")

    def _gen_all(self) -> None:
        failed = 0
        for scenario in registry.SCENARIOS:
            if not self._generate(scenario.key):
                failed += 1
        self._mark_dirty()
        self._refresh_output(keep_scroll=False)
        self._refresh_doc()
        total = len(registry.SCENARIOS)
        if failed:
            self._flash(f"已生成 {total - failed}/{total} 个场景，另有 {failed} 个场景参数有误")
        else:
            self._flash(f"已生成全部 {total} 个场景的命令")

    def _generate(self, scenario_key: str) -> bool:
        scenario = registry.get(scenario_key)
        params = self._collect(scenario)
        self.store[(self.vendor_key, scenario_key)] = params

        if not supports(self.vendor_key, scenario_key):
            self.errors[scenario_key] = f"{self._vendor().name} 不支持本场景"
            self.results.pop(scenario_key, None)
            self._update_card_state(scenario_key)
            return False

        try:
            result = generate(self.vendor_key, scenario_key, params, scenario)
        except ValueError as exc:
            self.errors[scenario_key] = str(exc)
            self.results.pop(scenario_key, None)
            self._update_card_state(scenario_key)
            return False
        except Exception as exc:  # noqa: BLE001
            self.errors[scenario_key] = f"生成失败：{exc}"
            self.results.pop(scenario_key, None)
            self._update_card_state(scenario_key)
            return False

        self.results[scenario_key] = result
        self.errors.pop(scenario_key, None)
        if scenario_key not in self.order:
            self.order.append(scenario_key)
        self._update_card_state(scenario_key)
        return True

    def _update_card_state(self, scenario_key: str) -> None:
        label = self.cards.get(scenario_key)
        if label is None:
            return
        if scenario_key in self.errors:
            label.configure(text=f"✗ 未生成：{self.errors[scenario_key]}",
                            style="CardWarn.TLabel")
        elif scenario_key in self.results:
            lines = sum(len(b.lines) for b in self.results[scenario_key].blocks)
            label.configure(text=f"✓ 已生成，本段 {lines} 行命令，已在左侧脚本区",
                            style="CardFaint.TLabel")
        else:
            label.configure(text="尚未生成。填好参数后点右上角「生成脚本」。",
                            style="CardFaint.TLabel")

    def _sync_gen_buttons(self) -> None:
        """厂商换了之后，不支持该场景的按钮要跟着禁用。"""
        for scenario in registry.SCENARIOS:
            btn = self.gen_buttons.get(scenario.key)
            if btn is None:
                continue
            if supports(self.vendor_key, scenario.key):
                btn.state(["!disabled"])
            else:
                btn.state(["disabled"])

    def _clear_all(self) -> None:
        self.results.clear()
        self.errors.clear()
        self.order.clear()
        for scenario in registry.SCENARIOS:
            self._update_card_state(scenario.key)
        self._refresh_output()
        self._refresh_doc()
        self._mark_dirty()
        self._flash("已清空脚本区与生成记录")

    def _on_vendor_change(self) -> None:
        """换厂商：先把当前厂商填的参数存下来，再回填新厂商并重算已生成的场景。"""
        key = self.vendor_var.get()
        if key == self.vendor_key:
            return
        self._save_fields()
        self.convert_note = []      # 手工换厂商，上一次转换的说明不再适用
        self._switch_vendor(key)
        self._flash(f"已切换到 {self._vendor().name}，"
                    f"重新生成了 {len(self.order)} 个场景")

    def _switch_vendor(self, key: str) -> None:
        """切到另一家厂商，并把已生成的场景按新厂商重算一遍。

        调用前必须先 ``_save_fields()``，否则当前厂商刚填的值还留在控件里、
        没进 ``store``，切走就丢了。转换流程复用这一段，所以它单独拆出来。
        """
        self.vendor_key = key
        self.vendor_var.set(key)    # Radiobutton 的 command 只在点击时触发，这里不会误触

        vendor = self._vendor()
        self.strip_hint.configure(text=f"{vendor.platform}　|　{vendor.port_hint}")

        # 表单内容不随厂商变，但要换成该厂商上次填过的值
        self._restore_fields()

        # 已经生成过的场景，按新厂商重新算一遍
        self.results.clear()
        self.errors.clear()
        todo = list(self.order)
        self._sync_gen_buttons()
        for scenario in registry.SCENARIOS:
            self.cards[scenario.key].configure(text="")
            self._update_card_state(scenario.key)
        for scenario_key in todo:
            self._generate(scenario_key)

        # 基线是按厂商语法解析的，换厂商必须作废重来
        self.baseline = None
        self.baseline_source = ""
        if hasattr(self, "baseline_btn"):
            self._paint_mode_buttons()
        if hasattr(self, "diag_out"):
            self._show_case()

        # AI 页的「生成厂商」跟着走：主页选了华为，回 AI 页还按 H3C 抽参数，
        # 端口编号就会对不上
        if getattr(self, "ai_page", None) is not None:
            self.ai_page.sync_vendor()

        self._refresh_output(keep_scroll=False)
        self._refresh_doc()

    # ==================================================================
    # 一键转换：整套参数搬到另一家厂商
    # ==================================================================

    def _refresh_convert_menu(self) -> None:
        """菜单弹出前现算目标列表 —— 当前厂商不列进去。"""
        menu = self.convert_menu
        menu.delete(0, "end")
        menu.configure(background=T.PANEL, foreground=T.FG,
                       activebackground=T.SEL_BG, activeforeground=T.ACCENT,
                       bd=0, relief="flat", activeborderwidth=0,
                       font=T.FONT_UI_SM)
        targets = [v for v in VENDORS if v.key != self.vendor_key]
        if not targets:
            return
        for vendor in targets:
            menu.add_command(label=f"转换为 {vendor.name}",
                             command=lambda k=vendor.key: self._convert_to(k))
        menu.add_separator()
        menu.add_command(label=f"共 {len(self.order)} 个已生成场景会一起转换",
                         state="disabled")

    def _convert_to(self, dst_key: str) -> None:
        """把当前厂商已生成的场景整套转换到另一家厂商。

        只转换「已经生成过」的场景 —— 没生成的场景参数可能压根没填过，
        搬过去只会带一堆默认值。
        """
        if dst_key == self.vendor_key:
            return

        self._save_fields()
        todo = [k for k in self.order if k in self.results]
        if not todo:
            messagebox.showinfo(
                "还没有可转换的内容",
                "左侧脚本区还是空的。\n\n"
                "先在右侧填好参数，点某个场景的「生成脚本」，"
                "或右上角的「生成全部场景」，再回来做转换。",
                parent=self)
            return

        src_key = self.vendor_key
        src = self._vendor()
        dst = next(v for v in VENDORS if v.key == dst_key)

        report: list = []
        moved = 0
        skipped: list = []
        for scenario_key in todo:
            scenario = registry.get(scenario_key)
            if not supports(dst_key, scenario_key):
                skipped.append(scenario.name)
                continue
            params = self.store.get((src_key, scenario_key))
            if params is None:
                params = {p.key: p.default for p in scenario.params}
            new_params, pairs = convert.convert_params(
                params, scenario, src_key, dst_key)
            self.store[(dst_key, scenario_key)] = new_params
            moved += 1
            for pair in pairs:
                if pair not in report:
                    report.append(pair)

        if not moved:
            messagebox.showinfo(
                "没有可转换的场景",
                f"{dst.name} 不支持当前已生成的全部场景，没有可搬过去的内容。",
                parent=self)
            return

        self.convert_note = convert.heading_lines(src.name, dst.name, report)
        self._switch_vendor(dst_key)

        msg = f"已把 {moved} 个场景转换到 {dst.name}"
        if report:
            msg += f"，{len(report)} 处端口编号已换算，请核对面板编号"
        if skipped:
            msg += f"；{len(skipped)} 个场景 {dst.name} 不支持，已跳过"
        self._flash(msg)
        self._mark_dirty()

    # ==================================================================
    # 方案文件：保存 / 打开
    # ==================================================================

    def _refresh_title(self) -> None:
        name = self.project_path.name if self.project_path else "未命名方案"
        self.title(f"{APP_NAME} · 交换机配置命令一键生成 — {name}"
                   + (" *" if self.dirty else ""))

    def _mark_dirty(self, *_args) -> None:
        """有未保存的改动。回填 / 加载方案期间由 _silent 挡住。"""
        if self._silent or self.dirty:
            return
        self.dirty = True
        self._refresh_title()

    def _clear_dirty(self) -> None:
        self.dirty = False
        self._refresh_title()

    def _bind_dirty_tracking(self) -> None:
        """给每个参数控件挂上变更监听。

        多层文本框用 <<Modified>> 事件；下拉框用 <<ComboboxSelected>>；
        输入框和勾选框背后是 Variable，用 trace 监听变量写入——
        这样「打了字但还没点生成」也算改动，关窗口时不会被悄悄丢掉。
        """
        for scenario_widgets in self.widgets.values():
            for kind, widget in scenario_widgets.values():
                if kind == "text":
                    # 回填时 insert 已经把 modified 置位了，先复位，
                    # 否则用户第一次输入不会产生 False→True 的跳变，事件不触发
                    widget.edit_modified(False)
                    widget.bind("<<Modified>>", self._on_text_modified, add="+")
                elif kind == "choice":
                    widget.bind("<<ComboboxSelected>>", self._mark_dirty, add="+")
                else:
                    widget.trace_add("write", self._mark_dirty)

    def _on_text_modified(self, event) -> None:
        widget = event.widget
        if widget.edit_modified():
            widget.edit_modified(False)   # 一次性事件，复位后下次才能再触发
            self._mark_dirty()

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-s>", self._shortcut_save)
        self.bind("<Control-Shift-S>", self._shortcut_save_as)
        self.bind("<Control-o>", self._shortcut_open)
        # Tk 的 Text 类自带 <Control-o>（插入新行）绑定，会抢在 toplevel 之前执行；
        # 在每个 Text 上重绑并返回 break，Ctrl+O 才只做「打开方案」
        for widget in self._walk(widget_class=tk.Text):
            widget.bind("<Control-o>", self._shortcut_open)

    def _walk(self, parent=None, widget_class=None) -> list:
        node = self if parent is None else parent
        found: list = []
        for child in node.winfo_children():
            if widget_class is None or isinstance(child, widget_class):
                found.append(child)
            found.extend(self._walk(child, widget_class))
        return found

    def _shortcut_save(self, _event=None):
        self._save_project()
        return "break"

    def _shortcut_save_as(self, _event=None):
        self._save_project(force_dialog=True)
        return "break"

    def _shortcut_open(self, _event=None):
        self._open_project()
        return "break"

    def _collect_project(self) -> project.Project:
        """把界面状态收成一个 Project。

        先把当前厂商所有场景的值从界面收进 store —— 用户很可能填了参数
        但没点「生成脚本」，这部分也要存下来，不然保存完再打开就丢了。
        """
        self._save_fields()

        params: dict = {}
        for (vendor_key, scenario_key), values in self.store.items():
            params.setdefault(vendor_key, {})[scenario_key] = dict(values)

        return project.Project(
            vendor=self.vendor_key,
            params=params,
            order=list(self.order),
            comments=bool(self.show_comment.get()),
            theme="dark" if self.dark else "light",
        )

    def _apply_project(self, plan: project.Project) -> None:
        """把方案灌进界面。

        先把状态写进内存，再整页重建 —— 参数值和厂商都要靠表单重建才能反映出来，
        逐个控件去 set 容易漏掉下拉框的显示文本映射。
        """
        self.store.clear()
        for vendor_key, scenes in plan.params.items():
            for scenario_key, values in scenes.items():
                self.store[(vendor_key, scenario_key)] = dict(values)

        valid = {v.key for v in VENDORS}
        self.vendor_key = plan.vendor if plan.vendor in valid else VENDORS[0].key
        self.vendor_var.set(self.vendor_key)   # Radiobutton 的 command 只在点击时触发，这里不会误触

        self.results.clear()
        self.errors.clear()
        self.order.clear()
        self.show_comment.set(bool(plan.comments))

        if (plan.theme == "dark") != self.dark:
            self.dark = plan.theme == "dark"
            T.apply(self, self.scale, self.dark)

        self._silent = True
        try:
            self._build_all()       # _restore_fields 会从刚填好的 store 取值
        finally:
            self._silent = False

        known = {s.key for s in registry.SCENARIOS}
        for scenario_key in plan.order:
            if scenario_key in known:
                self._generate(scenario_key)

        self._refresh_output(keep_scroll=False)
        self._refresh_doc()
        self._set_status()

    def _save_project(self, force_dialog: bool = False) -> bool:
        """保存方案。返回 True 表示确实写盘成功了。"""
        # 已经有文件、又没有改动：不必再原样写一遍。
        # 这一步很关键——早先这里是静默覆盖，用户点了按钮看不到任何反应，
        # 会以为按钮坏了。
        if not force_dialog and self.project_path and not self.dirty:
            self._flash(f"没有未保存的改动，{self.project_path.name} 已是最新", 6000)
            return True

        target = None if force_dialog else self.project_path
        if target is None:
            stem = (self.project_path.stem if self.project_path
                    else f"{self._vendor().name.split()[0]}_配置方案")
            chosen = filedialog.asksaveasfilename(
                title="保存方案",
                defaultextension=project.SUFFIX,
                initialfile=stem + project.SUFFIX,
                filetypes=project.FILETYPES,
            )
            if not chosen:
                return False
            target = pathlib.Path(chosen)

        try:
            project.to_file(target, self._collect_project())
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return False

        self.project_path = target
        self._clear_dirty()
        # 用绿色显示，8 秒。常驻状态栏是灰色小字，一闪而过的灰字等于没提示
        self._flash(f"✓ 已保存 {target.name}　"
                    f"（Ctrl+S 再存一次，Ctrl+Shift+S 另存）",
                    8000, "OK.TLabel")
        return True

    def _open_project(self) -> None:
        if not self._confirm_discard():
            return

        chosen = filedialog.askopenfilename(
            title="打开方案", filetypes=project.FILETYPES)
        if chosen:
            self.open_path(chosen)

    def open_path(self, path) -> bool:
        """按路径打开方案。

        命令行参数、拖到 exe 图标上、拖进窗口、菜单里点「打开方案」，
        四条路最后都走到这里，行为保持一致。
        """
        target = pathlib.Path(path)
        try:
            plan, warnings = project.from_file(target)
        except project.ProjectError as exc:
            messagebox.showerror("打不开这个方案", f"{target}\n\n{exc}")
            return False

        self._apply_project(plan)
        self.project_path = target
        self._clear_dirty()
        self._flash(f"已打开方案：{target.name}（{plan.count()} 个场景带参数，"
                    f"重新生成了 {len(self.order)} 个）")

        if warnings:
            messagebox.showwarning(
                "方案打开了，但有几处被跳过",
                f"这个方案里有 {len(warnings)} 处内容当前版本不认识，已忽略：\n\n"
                + "\n".join(f"· {w}" for w in warnings[:8])
                + ("\n…" if len(warnings) > 8 else "")
                + "\n\n常见原因：方案由更新版本保存，或者手改过文件。",
            )
        return True

    def _confirm_discard(self) -> bool:
        """有未保存改动时先问一句。True 表示可以继续往下走。"""
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "有未保存的改动",
            "当前方案的改动还没保存。\n\n"
            "「是」保存后继续，\n"
            "「否」放弃改动，\n"
            "「取消」什么都不做。",
        )
        if answer is None:
            return False
        if answer:
            return self._save_project()
        return True

    def _on_files_dropped(self, paths: list) -> None:
        """文件被拖进窗口。只认方案文件，别的给个提示不动当前状态。"""
        target = next((p for p in paths
                       if str(p).lower().endswith(project.SUFFIX)), None)
        if target is None:
            names = "、".join(pathlib.Path(p).name for p in paths[:3])
            self._flash(f"拖进来的是 {names}，不是方案文件（要 {project.SUFFIX}）")
            return
        if not self._confirm_discard():
            return
        self.open_path(target)

    def _on_close(self) -> None:
        if self._confirm_discard():
            drop = getattr(self, "drop", None)
            if drop is not None:
                drop.disable()           # 把窗口过程还回去，别留野指针
            self.destroy()

    # ==================================================================
    # 渲染：左侧脚本区
    # ==================================================================

    def _active_keys(self) -> list:
        return [s.key for s in registry.SCENARIOS if s.key in self.results]

    def _script_text(self) -> str:
        keys = self._active_keys()
        if not keys:
            return ""
        if not self.show_comment.get():
            # 取消勾选「保留注释」= 只要命令，整段可以直接粘到设备上跑
            return self._plain_text(keys)

        vendor = self._vendor()
        width = 68
        out = [
            "#" + "=" * width,
            f"# {vendor.name} · {vendor.platform}",
            f"# {APP_NAME} · 一键配置脚本 · 共 {len(keys)} 个场景",
            "# 下发前请核对软件版本与端口名",
            "#" + "=" * width,
        ]
        if self.convert_note:
            out.append("#" + "-" * width)
            for line in self.convert_note:
                out.append(f"# [转换] {line}")
            out.append("#" + "-" * width)
        for index, key in enumerate(keys, 1):
            scenario = registry.get(key)
            result = self.results[key]
            out.append("")
            out.append(RULE)
            out.append(f"# 【{index}】{scenario.name}　（{scenario.group}）")
            out.append(RULE)
            for block in result.blocks:
                if block.title:
                    out.append("")
                    out.append(f"# ---- {block.title} ----")
                if block.comment:
                    for line in block.comment.splitlines():
                        out.append(f"# {line}" if line.strip() else "#")
                out.extend(block.lines)
            if result.warnings:
                out.append("")
                for item in result.warnings:
                    out.append(f"# [风险] {item}")
            if result.notes:
                out.append("")
                for item in result.notes:
                    out.append(f"# [提示] {item}")
        out.append("")
        out.append("#" + "=" * width)
        out.append("# 说明：命令格式仅供参考！相同配置核对后复制粘贴到设备即可。")
        out.append("#" + "=" * width)

        failed = [k for k in self.order if k in self.errors]
        if failed:
            out.append("")
            for key in failed:
                out.append(f"# [未生成] {registry.get(key).name}：{self.errors[key]}")
        # 多个场景拼在一起会有重复的 system-view 和好几次保存，
        # 同一条提示也可能被 registry 和厂商模块各写一遍，这里统一收干净
        return "\n".join(
            compose.tidy_commands(compose.dedup_lines(out), self.vendor_key))

    def _plain_text(self, keys) -> str:
        """只输出命令本身：分节标题、说明、风险提示、IOS 的 ! 注释全部去掉。

        直接删以 # / ! 开头的行（两家 CLI 里这两个字符都不可能出现在命令开头），
        场景之间用空行隔开，末尾不留空行。
        """
        out: list[str] = []
        for key in keys:
            result = self.results[key]
            start = len(out)
            for block in result.blocks:
                for line in block.lines:
                    text = line.rstrip()
                    if not text.strip():
                        continue
                    if text.lstrip()[:1] in ("#", "!"):
                        continue
                    out.append(text)
            if len(out) > start:
                out.append("")
        while out and not out[-1].strip():
            out.pop()
        # 纯命令模式更需要整理：重复的进视图和保存命令在设备上看着很刺眼
        return "\n".join(compose.tidy_commands(out, self.vendor_key))

    def _refresh_output(self, keep_scroll: bool = True) -> None:
        text = self._current_text()
        pos = self.out.yview()[0] if keep_scroll else 0.0

        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        if not text:
            if self.script_mode == MODE_ROLLBACK:
                self.out.insert("end",
                                "# 回滚脚本由左侧的生成结果反推而来。\n"
                                "# 先在「配置脚本」里生成几段配置，再切回来就能看到对应的撤销命令。\n"
                                "# 回滚脚本会按原配置的逆序执行，可整体复制粘贴到设备。\n",
                                "note")
            elif self.script_mode == MODE_BLUEPRINT:
                self.out.insert("end",
                                "# 整机开局脚本把已生成的场景按开局依赖顺序重新排列。\n"
                                "# 先在「配置脚本」里生成要用的场景，再切回来就能看到编排结果。\n"
                                "# 顺序：堆叠 -> 管理面 -> VLAN 与端口 -> 聚合 -> 生成树 -> "
                                "网关 -> 路由 -> 安全 -> 运维。\n",
                                "note")
            else:
                self.out.insert("end", "# 左侧是生成结果，右侧是参数。\n"
                                       "# 填好某个场景的参数后，点它右上角的「生成脚本」，\n"
                                       f"# 或者在右上方点「生成全部场景」一次生成 "
                                       f"{len(registry.SCENARIOS)} 个场景。\n",
                                "note")
        else:
            for line in text.splitlines():
                self.out.insert("end", line + "\n", self._line_tag(line))
        self.out.configure(state="disabled")
        try:
            self.out.yview_moveto(pos)
        except tk.TclError:
            pass

        keys = self._active_keys()
        total = len(registry.SCENARIOS)
        cmd_lines = sum(len(b.lines) for k in keys for b in self.results[k].blocks)
        vendor = self._vendor()

        if self.script_mode == MODE_ROLLBACK:
            undo = getattr(self, "_last_rollback", None)
            n_lines = len(undo.blocks[0].lines) if undo and undo.blocks else 0
            self.script_meta.configure(text=f"回滚 {n_lines} 行")
            self.foot_meta.configure(
                text=f"当前厂商：{vendor.name}　|　回滚脚本，撤销 {len(keys)}/{total} 个场景"
                     f"（原配置合计 {cmd_lines} 行命令）")
        elif self.script_mode == MODE_BLUEPRINT:
            plan = getattr(self, "_last_blueprint", None)
            n_lines = sum(len(b.lines) for b in plan.blocks) if plan else 0
            info = blueprint.coverage({k: self.results[k] for k in keys}) if keys else None
            stages = info["stages"] if info else 0
            self.script_meta.configure(text=f"{stages} 个阶段")
            self.foot_meta.configure(
                text=f"当前厂商：{vendor.name}　|　整机开局：{stages} 个阶段 / "
                     f"{len(keys)} 个场景，合计 {n_lines} 行")
        else:
            self.script_meta.configure(text=f"{len(keys)}/{total} 个场景")
            self.foot_meta.configure(
                text=f"当前厂商：{vendor.name}　|　已生成 {len(keys)}/{total} 个场景，"
                     f"合计 {cmd_lines} 行命令")
        self._set_status()

    @staticmethod
    def _line_tag(line: str) -> str:
        text = line.lstrip()
        if not text or text[0] not in ("#", "!"):
            return "cmd"
        rest = text[1:]
        if rest.startswith((" [风险]", " [未生成]", " [近似]", " [人工确认]", " 说明：")):
            return "warn"
        if rest.startswith(("===", "---")):
            return "sep"
        if rest.startswith(" 【"):
            return "head"
        if rest.startswith(" ---- "):
            return "sub"
        return "cmt"

    # ==================================================================
    # 渲染：详解页
    # ==================================================================

    def _merged(self):
        keys = self._active_keys()
        if not keys:
            return None
        merged = GenResult(
            vendor_name=self._vendor().name,
            scenario_name=f"当前脚本区已生成的 {len(keys)} 个配置场景",
        )
        for key in keys:
            merged.blocks.extend(self.results[key].blocks)
            merged.notes.extend(self.results[key].notes)
            merged.warnings.extend(self.results[key].warnings)
        return merged

    def _doc_lines(self) -> list:
        mode = self.doc_mode.get()
        if mode == DOC_MANUAL:
            return explain.render_manual(self.vendor_key)
        if mode == DOC_COMPARE:
            return explain.render_compare()
        merged = self._merged()
        if merged is None:
            return ["脚本区还没有内容。",
                    "",
                    "回到「一键生成配置命令」页，填好参数后点某个场景的「生成脚本」，",
                    "或者点「生成全部场景」，然后回到这里就能看到逐条命令详解。",
                    "",
                    "也可以把上面的下拉框切成「该厂商命令手册」或「四厂商命令对照」，",
                    "那两种视图不依赖生成结果，随时可看。"]
        return explain.render_explanation(self.vendor_key, merged)

    def _refresh_doc(self) -> None:
        lines = self._doc_lines()
        self.doc.configure(state="normal")
        self.doc.delete("1.0", "end")
        for line in lines:
            m = self._CMP_RX.match(line)
            if m:
                # 厂商名与语法分色，语法保持等宽，读起来更像命令手册
                self.doc.insert("end", m.group(1) + m.group(2), "dim")
                self.doc.insert("end", "  " + m.group(3) + "\n", "syntax")
                continue
            self.doc.insert("end", line + "\n", self._doc_tag(line))
        self.doc.configure(state="disabled")

        merged = self._merged()
        if merged is None:
            self.doc_meta.configure(text="")
        else:
            hit, total, _miss = explain.coverage(self.vendor_key, merged)
            self.doc_meta.configure(text=f"命令详解覆盖 {hit}/{total} 行")

    @staticmethod
    def _doc_tag(line: str) -> str:
        if line.startswith("━━"):
            return "h1"
        if line.startswith("▌"):
            return "h2"
        if line.startswith("【") or line.startswith("  · "):
            return "title"
        if line.startswith("  语法"):
            return "syntax"
        if line.startswith("  注意"):
            return "caution"
        if line.startswith("  版本"):
            return "version"
        if line.startswith("  参数") or line.startswith("          "):
            return "dim"
        if line.startswith("  ──"):
            return "h2"
        if line.startswith("        "):
            return "syntax"
        if line.startswith("      "):
            return "compare"
        if line.startswith("   "):
            return "dim"
        return "body"

    # ==================================================================
    # 输出动作
    # ==================================================================

    def _copy(self) -> None:
        text = self._current_text()
        if not text:
            self._flash("脚本区还是空的，先生成一段吧")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        if self.script_mode == MODE_ROLLBACK:
            self._flash("回滚脚本已复制。下发前确认已存档现网配置")
        else:
            self._flash("脚本已复制到剪贴板，可直接粘贴到设备终端")

    def _copy_doc(self) -> None:
        text = self.doc.get("1.0", "end").rstrip("\n")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.doc_meta.configure(text="详解已复制到剪贴板")

    def _export(self) -> None:
        text = self._current_text()
        if not text:
            self._flash("脚本区还是空的，没有可导出的内容")
            return
        suffix = "回滚脚本" if self.script_mode == MODE_ROLLBACK else "一键配置脚本"
        default = f"{self._vendor().name.split()[0]}_{suffix}.txt"
        path = filedialog.asksaveasfilename(
            title=f"导出{suffix}",
            defaultextension=".txt",
            initialfile=default,
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            pathlib.Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self._flash(f"已导出：{path}")

    def _export_changekit(self) -> None:
        """导出一份变更包：现状 / 命令 / 回滚 / 验收 / 影响范围。

        写变更单的时候要的五样东西，这里一次给全。散在四个页签里挨个点、
        再手工拼到一起，是交付时最烦的一步。
        """
        keys = self._active_keys()
        if not keys:
            messagebox.showinfo("没有内容",
                                "脚本区还是空的。先生成几个场景，再导变更包。",
                                parent=self)
            return

        results = {k: self.results[k] for k in keys if k in self.results}
        if not results:
            messagebox.showinfo("没有内容", "还没有生成成功的场景。", parent=self)
            return

        items = [(k, self.store.get((self.vendor_key, k), {})) for k in keys
                 if k in results]
        vendor_name = self._vendor().name

        try:
            lines = changekit.build(self.vendor_key, vendor_name, items, results,
                                    snap=getattr(self, "_imported_snap", None))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("生成变更包失败", str(exc), parent=self)
            return

        path = filedialog.asksaveasfilename(
            title="保存变更包",
            defaultextension=".txt",
            initialfile=changekit.filename(vendor_name, items),
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return

        try:
            pathlib.Path(path).write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return

        has_snap = getattr(self, "_imported_snap", None) is not None
        note = "" if has_snap else "（没导入现网配置，「变更前状态」那段是空的）"
        self._flash(f"变更包已保存：{pathlib.Path(path).name} {note}",
                    style="OK.TLabel")

    # ==================================================================
    # 状态栏 / 杂项
    # ==================================================================

    def _status_text(self) -> str:
        keys = self._active_keys()
        vendor = self._vendor()
        parts = [f"当前版本 {APP_VERSION}", f"当前厂商：{vendor.name}"]
        if self.project_path:
            parts.append(f"方案：{self.project_path.name}"
                         + ("（未保存）" if self.dirty else ""))
        if keys:
            merged = self._merged()
            hit, total, _miss = explain.coverage(self.vendor_key, merged)
            parts.append(f"已生成 {len(keys)}/{len(registry.SCENARIOS)} 个场景")
            parts.append(f"该厂商命令详解覆盖 {hit}/{total} 行")
        else:
            parts.append("尚未生成命令")
        parts.append("深色主题" if self.dark else "浅色主题")
        return "　|　".join(parts)

    def _set_status(self) -> None:
        if hasattr(self, "status"):
            self.status.configure(text=self._status_text(), style="Faint.TLabel")

    def _flash(self, message: str, ms: int = 5000, style: str = "") -> None:
        """状态栏临时提示，过一会儿自动退回常驻信息。

        ``style`` 可以用 ``OK.TLabel`` 这类样式把提示变醒目，
        保存成功这种「用户必须看见」的反馈用得上——常驻状态栏是灰色小字，
        一闪而过的灰字等于没提示。
        """
        if not hasattr(self, "status"):
            return
        self.status.configure(text=message, style=style or "Faint.TLabel")
        if self._status_timer:
            self.after_cancel(self._status_timer)
        self._status_timer = self.after(ms, self._set_status)

    def _help_lines(self) -> list:
        vendor = self._vendor()
        return [
            "━━ 这个工具箱是干什么的 ━━",
            "",
            "把「配一台交换机」这件事拆成 %d 个常见场景，选好厂商、填好参数，" % len(registry.SCENARIOS),
            "一键生成可以直接粘贴到设备的配置命令；同一批命令还能逐条看详解，",
            "或者横着对比四家厂商同一件事分别怎么写。",
            "",
            "覆盖厂商：思科 IOS、华为 VRP、H3C Comware、锐捷 RGOS。",
            "",
            "━━ 怎么用 ━━",
            "",
            "1. 在「一键生成配置命令」页右上角选设备厂商。",
            "2. 在右边一页到底的参数区里，找到你要配的场景，填参数。",
            "3. 点这个场景右上角的「生成脚本」，这段命令就落到左边的脚本区。",
            "   想一次全要，就点最上面的「生成全部场景」。",
            "4. 点「复制脚本」直接粘到设备终端，或点「导出」存成 txt。",
            "5. 想搞清楚每条命令在干嘛，切到「设备命令详解」页。",
            "",
            "━━ AI 助手（可选，不配置也不影响使用） ━━",
            "",
            "「AI 助手」页可以说一句话就把参数填进表单：说「给 VLAN 10 20 30 划 8 个",
            "access 口」，它识别场景、抽出参数、回填到主页面并生成命令。",
            "",
            "不配置就完全不联网。要用的话点那一页右上角「设置」，填接口地址、模型名",
            "和密钥。地址填 OpenAI 兼容的就行——DeepSeek、通义、智谱、本地 ollama",
            "都能用，换一家只改地址，不用换版本。密钥存在 %APPDATA% 下，用 Windows",
            "自带的 DPAPI 加密，只有本机能解开，也不会写进方案文件。",
            "",
            "有一条要记住：AI 只负责填参数，命令始终由本工具的模板拼出来。",
            "它不写命令，也不改你的配置。",
            "",
            "━━ 配置体检：三层检查 ━━",
            "",
            "把 display current-configuration / show running-config 的输出整段粘到",
            "「配置体检」页左边，点「开始体检」，右边会分三层给结论：",
            "",
            "第一层 安全体检（离线）：SNMP 团体名、VLAN1 当管理口、没配 NTP、",
            "        空闲端口没关、密码强度这些，出健康分和可直接粘的修复命令。",
            "第二层 语法校对（离线）：命令跑没跑在对的视图、基础 ACL 是不是带了",
            "        协议字段、端口编号符不符合厂商惯例、关键字有没有拼错。",
            "第三层 AI 补充（可选，要联网）：前两层都是单点判定，这一层补需要读懂",
            "        整段配置才能发现的问题——配置互相冲突、缺配套命令、策略顺序。",
            "",
            "前两层不联网也能跑，粘完点一下就有结果。第三层要在左下角勾选，",
            "勾了才会把脱敏过的文本发给模型，没勾就一个字节都不出本机。",
            "",
            "━━ 想要「复制即用」的纯命令 ━━",
            "",
            "脚本区左下角有个「保留注释」勾选框，默认是勾上的，这时脚本里有分节标题、",
            "逐段说明和红色的风险提示，方便你自己看。",
            "",
            "把勾去掉，这些注释会连同思科的 ! 注释行一起被清掉，",
            "只留命令本身，场景之间用空行隔开——这时候点「复制脚本」，",
            "整段粘到设备上就能直接跑，不用逐行删注释。",
            "「导出」出来的 txt 也是同样的内容。",
            "",
            "每个厂商的参数是分开保存的，来回切换厂商不会把填好的值冲掉；",
            "已经生成过的场景，切换厂商后会自动按新厂商重算一遍。",
            "",
            "━━ 多个场景会合并成一份 ━━",
            "",
            "生成了好几个场景时，脚本区里的命令会自动整理，不是简单堆在一起：",
            "重复的 system-view 只留第一个，保存只留最后一次，",
            "同一条提示不会出现两遍。所以整段粘到设备上，不会出现保存三遍这种怪东西。",
            "",
            "思科和锐捷的 configure terminal 不会删。它们的 show 必须在特权模式下才能跑，",
            "中间的 end 和 configure terminal 是成对的，删了会让命令落到错误的视图里。",
            "",
            "━━ 把填好的一屏参数存成方案 ━━",
            "",
            "右上角有「保存方案」和「打开方案」。存下来的是一份 .json 文件，",
            "里面是四个厂商全部场景的参数、已生成的场景、注释开关和主题，",
            "下次打开接着改，不用重新填一遍。",
            "",
            "快捷键：Ctrl+S 保存，Ctrl+Shift+S 另存为，Ctrl+O 打开。",
            "标题栏会显示方案名，有未保存的改动时后面跟一个 *。",
            "",
            "另外两种偷懒的打开方式：",
            "· 把 .json 方案文件直接拖进这个窗口，松手就打开了",
            "· 把文件拖到 YangSwitchKit.exe 的图标上，效果一样",
            "",
            "存成 .json 是有意为之——纯文本，能进 git，能直接用记事本改，",
            "两台设备要部署同一套规划时，把这份文件发给同事就行。",
            "方案文件不是配置脚本，两者别混：方案给本工具打开，",
            "脚本（.txt）才是粘到设备上执行的。",
            "",
            "━━ 一整套开局怎么串：整机开局 ━━",
            "",
            "单独生成 29 段命令，粘的时候顺序错了是要出问题的：",
            "先配接口再建 VLAN，H3C 会直接告诉你 VLAN 不存在；",
            "先配生成树再配聚合，根桥位置就是错的。",
            "",
            "「整机开局」把已经生成的场景按真实依赖顺序重新排列：",
            "",
            "  堆叠 → 管理面 → VLAN 与端口 → 聚合 → 生成树",
            "       → 网关 → 路由 → 安全 → 运维 → 保存",
            "",
            "几处硬约束是有意这么排的：堆叠会重启设备所以放最前；",
            "管理面先于业务，否则中途失联就回不来了；聚合先于生成树，",
            "否则两条链路会被当成环路。",
            "",
            "干净的段落只保留一条 system-view，保存动作也只在最后出现一次；",
            "没生成的阶段会跳过，但管理面和 VLAN 这两段缺失会直接报风险 ——",
            "少了它们，开局后的设备是登不上去的。",
            "",
            "━━ 接手一台设备先做什么：配置体检 ━━",
            "",
            "「配置体检」页是把设备上 display current-configuration（思科/锐捷用",
            "show running-config）的输出整段粘进去，工具跑一遍规则，给出一份",
            "「风险 / 通过」报告和健康分，每条风险附上可直接用的修复命令。",
            "",
            "体检覆盖：远程管理面（Telnet、HTTP、SSH）、SNMP 团体字与版本、",
            "口令是否明文保存、NTP 与日志外送、VLAN 1 的用法、生成树边缘端口、",
            "闲置端口是否关闭、认证方式、DHCP Snooping 与风暴抑制。",
            "",
            "两个要留意的地方：",
            "",
            "· 工具会自动识别厂商，不用你选。",
            "· 如果粘的配置不足 40 行，报告会提示「配置可能不完整」，",
            "  并把「没查到」类的结论降级 —— 片段里没看到 NTP，不等于设备没配。",
            "",
            "体检只读这段文本，不连设备、不上传，结论仅代表配置本身。",
            "分数用来横向比对多台设备，不是测评结论。",
            "",
            "━━ 两台设备对不对得上：配置对比 ━━",
            "",
            "「配置对比」页左右各粘一份配置，比的是「同一件事两边写得一样不一样」。",
            "",
            "不是文本 diff —— 那种比对会被启动时间、保存时间、命令顺序这些噪声淹掉。",
            "这里先把配置展平成「路径 -> 取值」再比，所以：",
            "",
            "  · 命令顺序不同，不算差异",
            "  · port trunk permit vlan 10 20 和 10 20 30 会报「取值不同」，",
            "    而不是报成两条互不相关的配置",
            "  · 设备名、管理地址、堆叠优先级这类天生该不一样的东西，",
            "    单独归到「预期差异」，不干扰你判断",
            "",
            "典型用法：IRF / VSU / 堆叠双机核对主备是否一致；变更前后存档对比；",
            "接手别人的网络时，看两台设备为什么表现不一样。",
            "",
            "想拿自己生成的配置和现网比，点「A 侧填入当前脚本区」即可。",
            "",
            "━━ 排障时不知道该敲什么：故障排查 ━━",
            "",
            "「故障排查」页不是生成配置，是回答「现象是这样，接下来查什么」。",
            "",
            "左边选现象，右边给步骤。每一步写三件事：查什么、用什么命令、",
            "结果怎么判读，以及判读之后该往哪走 —— 而不是一句「检查配置是否正确」。",
            "",
            "目前覆盖六个最常见、也最容易查偏的现象：",
            "",
            "  VLAN 间不通 · 链路聚合起不来 · STP 震荡卡顿",
            "  终端拿不到 IP · OSPF 邻居起不来 · 端口 UP 但业务不通",
            "",
            "命令跟着当前厂商走：H3C / 华为给 display，思科 / 锐捷给 show。",
            "每个案例末尾还列了几个「看起来像同一个问题、其实不是」的坑，",
            "那些地方最容易白查半天。",
            "",
            "━━ 配错了怎么办：回滚脚本 ━━",
            "",
            "脚本区上方有「配置脚本 / 回滚脚本」两个切换按钮。",
            "切到「回滚脚本」，工具会把你当前脚本区里的配置反推成一份撤销脚本，",
            "按逆序排列 —— 后配的先撤，最后一条是保存。",
            "",
            "哪些能放心撤、哪些要自己看一眼，脚本里标得很清楚：",
            "",
            "· 没有任何标记的：撤销写法是确定的，可以直接执行。",
            "· 标了 [近似] 的：撤销后回到设备默认值，可能和你原来的配置不一样。",
            "  典型的是 trunk / access 模式这类「改之前是什么」工具没法知道的命令。",
            "· 标了 [人工确认] 的：工具推导不出撤销写法，比如 vpn-target、snmp 的",
            "  target-host。这些行本身是注释、设备不会执行，留给你手工处理。",
            "",
            "回滚脚本的注释符会跟着厂商变：H3C / 华为用 #，思科 / 锐捷用 !。",
            "这一点很重要 —— 思科的 # 不是注释符，混进脚本会被当成命令执行。",
            "",
            "执行回滚之前，务必先存档一份现网配置。",
            "",
            "━━ 手边只有手机 / 电脑不让装 exe ━━",
            "",
            "同一套命令库还导出了一份单文件网页：Yang-SwitchKit-Web.html。",
            "双击用浏览器打开就能看，不用安装任何东西，Mac 和手机也能用。",
            "",
            "网页版里有：29 个场景的四厂商命令示例、逐条命令详解、",
            "四厂商对照、故障排查步骤。命令内容和这个 exe 完全同源 ——",
            "都是同一个命令库导出的，不存在「网页对、exe 错」这种事。",
            "",
            "但要清楚它的边界：**网页版只做查阅，不做参数化生成**。",
            "要按自己的 VLAN 号、端口号生成命令，还是用这个 exe。",
            "",
            "手机上打开是能用的，但不用指望它替代电脑 —— 真调设备的时候，",
            "一只手要在键盘上敲、一只手要翻笔记，屏幕再大也不够用。",
            "手机上真正的用处是：机房里没带电脑，临时查一条命令怎么写。",
            "",
            "━━ 命令是怎么保证正确的 ━━",
            "",
            "命令不是凭印象写的，是逐条对着各厂商官方命令手册核对的。",
            "核对过程中确实查出并修掉了 5 处会报错或静默丢字段的写法，",
            "明细见「核验说明」页。",
            "",
            "另外生成结果里的每一行，都有对应的详解条目，覆盖率可以在状态栏看到。",
            "凡命令随版本变化的地方，结果里会明确标注「部分版本」，",
            "并提示你用 ? 补全确认，而不是假装四家各版本都通用。",
            "",
            "━━ 边界，请务必知道 ━━",
            "",
            "· 这个工具保证的是「命令拼写与语法结构」正确，",
            "  不保证「参数取值」适合你的现网——网段、VLAN、端口得你自己定。",
            "· 堆叠 / IRF / VSU 这类命令随型号和版本差异最大，给的是骨架，",
            "  下发前一定要对着本机型号的手册再核一遍。",
            "· 生成的配置里密码是明文，只适合开局使用，交付前请统一更换。",
            "· 下发前请先备份设备现有配置，并确认在维护窗口内操作。",
            "",
            f"当前厂商的接口命名习惯：{vendor.port_hint}",
        ]

    def _about(self) -> None:
        messagebox.showinfo(
            f"关于 {APP_NAME}",
            f"{APP_NAME}\n"
            f"交换机配置命令一键生成　{APP_VERSION}\n\n"
            f"覆盖 思科 / 华为 / H3C / 锐捷 四厂商，{len(registry.SCENARIOS)} 个高频配置场景，\n"
            "一键生成配置脚本，同时给出逐条设备命令详解与四厂商命令对照。\n\n"
            "每一段生成的配置都能反推出一份回滚脚本：按逆序撤销，\n"
            "并在撤销写法不确定的地方明确标注 [近似] 与 [人工确认]，\n"
            "不假装每条命令都能精确撤回。\n\n"
            "命令以各厂商官方命令手册为准逐条核对；\n"
            "凡随软件版本变化的命令，均在结果中标注版本差异，\n"
            "不假装四家、各版本都能通用。\n\n"
            "配置类操作有风险，下发前请核对端口名与版本，\n"
            "并提前备份设备现有配置。",
        )

    def _toggle_theme(self) -> None:
        """浅色 / 深色互换。颜色是控件创建时读的，所以整页重建一次。"""
        # 重建会把 Notebook 打回第一页，先记住用户停在哪一页
        try:
            current = self.pages.index(self.pages.select())
        except Exception:  # noqa: BLE001
            current = 0

        self.dark = not self.dark
        T.apply(self, self.scale, self.dark)
        self._build_all()

        try:
            self.pages.select(current)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 滚动与折行
    # ------------------------------------------------------------------

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_canvas_resize(self, event) -> None:
        self.canvas.itemconfigure(self._scroll_win, width=event.width)
        avail = max(T.px(340), event.width - T.px(56))
        for label, span in self.help_labels:
            try:
                label.configure(wraplength=int(avail / 2) - T.px(46) if span < 2
                                else avail - T.px(30))
            except tk.TclError:
                pass

    def _bind_wheel(self, widget) -> None:
        # 多行输入框自己会滚，别把滚轮事件抢走
        if isinstance(widget, tk.Text):
            return
        widget.bind("<MouseWheel>", self._on_wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)


def run(initial: str = "") -> None:
    """启动界面。

    ``initial`` 是要自动打开的方案文件路径，可以是空串。来源见 main.py：
    命令行参数、拖到 exe 图标上、双击已关联的文件。
    """
    app = App()
    if initial:
        # 等窗口先画出来再加载：加载失败要弹提示框，
        # 主窗口还没显示时单独弹个框在桌面上会很怪
        app.after(80, lambda: app.open_path(initial))
    app.mainloop()
