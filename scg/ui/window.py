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
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import __version__, compose, explain, project, registry, verify
from ..model import GenResult, Kind
from ..vendors import VENDORS, generate, supports
from . import dpi
from . import dnd
from . import theme as T

APP_NAME = "Yang-SwitchKit"
APP_VERSION = "V" + __version__

PAGE_MAIN = "一键生成配置命令"
PAGE_DOC = "设备命令详解"
PAGE_VERIFY = "核验说明"
PAGE_HELP = "使用说明"

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
        for child in list(self.children.values()):
            child.destroy()

        self.widgets.clear()
        self.choice_maps.clear()
        self.help_labels.clear()
        self.cards.clear()
        self.gen_buttons.clear()

        self.rowconfigure(2, weight=1)
        self._build_banner()
        tk.Frame(self, background=T.BORDER, height=1).grid(row=1, column=0, sticky="ew")
        self._build_pages()
        self._build_status()

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
        self.page_verify = tk.Frame(self.pages, background=T.BG)
        self.page_help = tk.Frame(self.pages, background=T.BG)
        self.pages.add(self.page_main, text=PAGE_MAIN)
        self.pages.add(self.page_doc, text=PAGE_DOC)
        self.pages.add(self.page_verify, text=PAGE_VERIFY)
        self.pages.add(self.page_help, text=PAGE_HELP)

        self._build_main_page(self.page_main)
        self._build_doc_page(self.page_doc)
        self._build_text_page(self.page_verify, verify.render_note())
        self._build_text_page(self.page_help, self._help_lines())

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
        pane.rowconfigure(1, weight=1)

        # 标题条：参考应用左栏那个「一键配置脚本」页签
        head = tk.Frame(pane, background=T.ACCENT)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text="▌ 一键配置脚本", style="ScriptTab.TLabel").pack(side="left")
        self.script_meta = ttk.Label(head, text="", style="ScriptTab.TLabel")
        self.script_meta.pack(side="right", padx=(0, T.px(10)))

        self.out = tk.Text(pane, background=T.PANEL, foreground=T.C_CMD,
                           insertbackground=T.FG, font=T.FONT_MONO,
                           relief="flat", bd=0, wrap="none",
                           padx=T.px(12), pady=T.px(10),
                           selectbackground=T.SEL_BG, selectforeground=T.FG)
        self.out.grid(row=1, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(pane, orient="vertical", command=self.out.yview)
        vbar.grid(row=1, column=1, sticky="ns")
        hbar = ttk.Scrollbar(pane, orient="horizontal", command=self.out.xview)
        hbar.grid(row=2, column=0, sticky="ew")
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
        foot.grid(row=3, column=0, columnspan=2, sticky="ew",
                  padx=T.px(10), pady=(T.px(4), T.px(10)))
        ttk.Button(foot, text="复制脚本", style="Accent.TButton",
                   command=self._copy).pack(side="left")
        ttk.Button(foot, text="导出", style="Ghost.TButton",
                   command=self._export).pack(side="left", padx=T.px(6))
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

        ttk.Button(strip, text="生成全部场景", style="Gen.TButton",
                   command=self._gen_all).grid(row=0, column=2, sticky="e")

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
        self.vendor_key = key

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

        self._refresh_output(keep_scroll=False)
        self._refresh_doc()
        self._flash(f"已切换到 {vendor.name}，重新生成了 {len(self.order)} 个场景")

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
        text = self._script_text()
        pos = self.out.yview()[0] if keep_scroll else 0.0

        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        if not text:
            self.out.insert("end", "# 左侧是生成结果，右侧是参数。\n"
                                   "# 填好某个场景的参数后，点它右上角的「生成脚本」，\n"
                                   f"# 或者在右上方点「生成全部场景」一次生成 {len(registry.SCENARIOS)} 个场景。\n",
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
        self.script_meta.configure(text=f"{len(keys)}/{total} 个场景")
        cmd_lines = sum(len(b.lines) for k in keys for b in self.results[k].blocks)
        self.foot_meta.configure(
            text=f"当前厂商：{self._vendor().name}　|　已生成 {len(keys)}/{total} 个场景，"
                 f"合计 {cmd_lines} 行命令")
        self._set_status()

    @staticmethod
    def _line_tag(line: str) -> str:
        if line.startswith("!") or line.startswith("#!"):
            # 思科 / 锐捷的 ! 也是注释，跟 # 一样按注释上色
            return "cmt"
        if line.startswith("#===") or line.startswith("#---"):
            return "sep"
        if line.startswith("# 【"):
            return "head"
        if line.startswith("# ----"):
            return "sub"
        if line.startswith("# [风险]") or line.startswith("# [未生成]"):
            return "warn"
        if line.startswith("# 说明："):
            return "warn"
        if line.startswith("#"):
            return "cmt"
        return "cmd"

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
        text = self._script_text()
        if not text:
            self._flash("脚本区还是空的，先生成一段吧")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._flash("脚本已复制到剪贴板，可直接粘贴到设备终端")

    def _copy_doc(self) -> None:
        text = self.doc.get("1.0", "end").rstrip("\n")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.doc_meta.configure(text="详解已复制到剪贴板")

    def _export(self) -> None:
        text = self._script_text()
        if not text:
            self._flash("脚本区还是空的，没有可导出的内容")
            return
        default = f"{self._vendor().name.split()[0]}_一键配置脚本.txt"
        path = filedialog.asksaveasfilename(
            title="导出配置脚本",
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
            "命令以各厂商官方命令手册为准逐条核对；\n"
            "凡随软件版本变化的命令，均在结果中标注版本差异，\n"
            "不假装四家、各版本都能通用。\n\n"
            "配置类操作有风险，下发前请核对端口名与版本，\n"
            "并提前备份设备现有配置。",
        )

    def _toggle_theme(self) -> None:
        """浅色 / 深色互换。颜色是控件创建时读的，所以整页重建一次。"""
        self.dark = not self.dark
        T.apply(self, self.scale, self.dark)
        self._build_all()

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
