"""界面配色与 ttk 主题。

提供两套调色板，默认浅色：
  - 浅色：白底卡片 + 浅灰窗口底，长时间看细节不累眼
  - 深色：原来的暗色方案，需要时可在顶栏一键切回

切换主题时重新赋值本模块的模块级颜色常量，由调用方负责重建控件
（见 ``ui/window.py`` 的 ``_toggle_theme``）。间距一律走 ``px()``，按屏幕 DPI 缩放。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# --------------------------------------------------------------------------
# 调色板
# --------------------------------------------------------------------------

_LIGHT: dict = {
    "BG": "#EDEFF3",          # 窗口底色
    "PANEL": "#FFFFFF",       # 卡片 / 顶栏 / 列表底
    "PANEL_ALT": "#F8F9FB",   # 文本区 / 命令区底
    "FIELD": "#FFFFFF",       # 输入框底
    "BORDER": "#D3D8E0",
    "BORDER_SOFT": "#E4E7EC",
    "FG": "#1F2328",
    "FG_DIM": "#4B5262",
    "FG_FAINT": "#6B7280",
    "SEL_BG": "#DBE7FF",
    "HOVER": "#E9EDF3",
    "TAB_IDLE": "#E4E8EE",
    "ACCENT": "#2563EB",
    "ACCENT_HOVER": "#3B7BF0",
    "ACCENT_PRESS": "#1D4FD8",
    "ON_ACCENT": "#FFFFFF",
    "C_HEAD": "#1D4ED8",
    "C_BLOCK": "#7C3AED",
    "C_CMD": "#1F2328",
    "C_CMT": "#15803D",
    "C_WARN": "#B45309",
    "C_ERR": "#B91C1C",
    "C_NOTE": "#6B7280",
    "OK": "#15803D",
    # 每个参数区的「生成脚本」按钮，沿用参考应用的绿色
    "GEN": "#16A34A",
    "GEN_HOVER": "#1FB457",
    "GEN_PRESS": "#11813F",
    "ON_GEN": "#FFFFFF",
}

_DARK: dict = {
    "BG": "#1B1B1F",
    "PANEL": "#232329",
    "PANEL_ALT": "#1F1F24",
    "FIELD": "#2A2A31",
    "BORDER": "#34343C",
    "BORDER_SOFT": "#2C2C33",
    "FG": "#E4E4E7",
    "FG_DIM": "#9E9EA9",
    "FG_FAINT": "#8C8C97",
    "SEL_BG": "#2E3A4C",
    "HOVER": "#2A2A32",
    "TAB_IDLE": "#1F1F24",
    "ACCENT": "#3B82F6",
    "ACCENT_HOVER": "#4C8FF7",
    "ACCENT_PRESS": "#2D6FD6",
    "ON_ACCENT": "#FFFFFF",
    "C_HEAD": "#7AA2F7",
    "C_BLOCK": "#A88BEB",
    "C_CMD": "#DCDCE2",
    "C_CMT": "#6E9E6E",
    "C_WARN": "#E0AF68",
    "C_ERR": "#F07178",
    "C_NOTE": "#8A8A96",
    "OK": "#7FBF7F",
    "GEN": "#2EA043",
    "GEN_HOVER": "#3CB757",
    "GEN_PRESS": "#268037",
    "ON_GEN": "#FFFFFF",
}

# 下面这些名字会被其它模块直接引用，由 _install() 赋值
BG = PANEL = PANEL_ALT = FIELD = BORDER = BORDER_SOFT = ""
FG = FG_DIM = FG_FAINT = SEL_BG = HOVER = TAB_IDLE = ""
ACCENT = ACCENT_HOVER = ACCENT_PRESS = ON_ACCENT = ""
C_HEAD = C_BLOCK = C_CMD = C_CMT = C_WARN = C_ERR = C_NOTE = OK = ""
GEN = GEN_HOVER = GEN_PRESS = ON_GEN = ""

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_UI_SM = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_MONO = ("Consolas", 10)

SCALE = 1.0
IS_DARK = False


def _install(palette: dict, dark: bool) -> None:
    global IS_DARK
    globals().update(palette)
    IS_DARK = dark


# 先装一套，保证模块级常量在任何时候都是有效颜色
_install(_LIGHT, False)


def px(value: float) -> int:
    """把按 96 DPI 设计的像素值换算到当前屏幕。"""
    return max(1, int(round(value * SCALE)))


def apply(root: tk.Misc, scale: float = 1.0, dark: bool = False) -> ttk.Style:
    """把主题应用到整个窗口。"""
    global SCALE
    SCALE = scale
    _install(_DARK if dark else _LIGHT, dark)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=BG)

    style.configure(".", background=BG, foreground=FG,
                    fieldbackground=FIELD, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL,
                    troughcolor=PANEL_ALT, focuscolor=ACCENT)

    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Alt.TFrame", background=PANEL_ALT)
    style.configure("Banner.TFrame", background=PANEL)

    style.configure("TLabel", background=BG, foreground=FG, font=FONT_UI)
    style.configure("Dim.TLabel", background=BG, foreground=FG_DIM, font=FONT_UI_SM)
    style.configure("Faint.TLabel", background=BG, foreground=FG_FAINT, font=FONT_UI_SM)
    style.configure("Title.TLabel", background=BG, foreground=FG, font=FONT_TITLE)
    style.configure("BannerTitle.TLabel", background=PANEL, foreground=FG,
                    font=("Microsoft YaHei UI", 13, "bold"))
    style.configure("BannerSub.TLabel", background=PANEL, foreground=FG_FAINT,
                    font=FONT_UI_SM)
    style.configure("OK.TLabel", background=BG, foreground=OK, font=FONT_UI_SM)
    style.configure("Sep.TFrame", background=BORDER_SOFT)
    style.configure("AccentBar.TFrame", background=ACCENT)

    # 选项卡：贴合参考应用「顶栏 + 分区内容」的观感
    style.configure("TNotebook", background=BG, bordercolor=BORDER_SOFT,
                    lightcolor=BG, darkcolor=BG, tabmargins=(px(4), px(6), px(0), 0))
    style.configure("TNotebook.Tab", background=TAB_IDLE, foreground=FG_DIM,
                    bordercolor=BORDER, lightcolor=TAB_IDLE,
                    darkcolor=TAB_IDLE, padding=(px(20), px(8)), font=FONT_UI)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL), ("active", HOVER)],
              foreground=[("selected", ACCENT)],
              lightcolor=[("selected", PANEL)],
              darkcolor=[("selected", PANEL)],
              expand=[("selected", (0, 0, 0, 0))])

    style.configure("TSeparator", background=BORDER)

    style.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                    bordercolor=BORDER, insertcolor=FG, padding=px(4))
    style.map("TEntry",
              fieldbackground=[("focus", FIELD)],
              bordercolor=[("focus", ACCENT)])

    style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                    foreground=FG, arrowcolor=FG_DIM, bordercolor=BORDER,
                    padding=px(3))
    style.map("TCombobox",
              fieldbackground=[("readonly", FIELD)],
              foreground=[("readonly", FG)],
              bordercolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)

    style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT_UI,
                    bordercolor=BORDER)
    style.map("TCheckbutton",
              background=[("active", BG)],
              foreground=[("active", FG)],
              indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
              bordercolor=[("!selected", BORDER)])

    style.configure("TButton", background=PANEL, foreground=FG,
                    bordercolor=BORDER, focuscolor=BG,
                    padding=(px(12), px(6)), font=FONT_UI, relief="flat")
    style.map("TButton",
              background=[("active", HOVER), ("pressed", TAB_IDLE)],
              bordercolor=[("active", BORDER)])

    style.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT,
                    bordercolor=ACCENT, padding=(px(14), px(7)),
                    font=FONT_UI, relief="flat")
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("pressed", ACCENT_PRESS)],
              foreground=[("disabled", FG_FAINT)])

    style.configure("Vendor.TButton", background=TAB_IDLE, foreground=FG_DIM,
                    bordercolor=BORDER, padding=(px(14), px(7)),
                    font=FONT_UI, relief="flat")
    style.map("Vendor.TButton", background=[("active", HOVER)],
              bordercolor=[("active", ACCENT)])
    style.configure("VendorOn.TButton", background=SEL_BG, foreground=ACCENT,
                    bordercolor=ACCENT, padding=(px(14), px(7)),
                    font=FONT_UI, relief="flat")
    style.map("VendorOn.TButton", background=[("active", SEL_BG)],
              foreground=[("active", ACCENT)])

    style.configure("Ghost.TButton", background=PANEL, foreground=FG_DIM,
                    bordercolor=BORDER, padding=(px(10), px(6)),
                    font=FONT_UI_SM, relief="flat")
    style.map("Ghost.TButton", background=[("active", HOVER)],
              foreground=[("active", FG)])

    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=FG, bordercolor=BORDER_SOFT,
                    rowheight=px(26), font=FONT_UI, relief="flat")
    style.map("Treeview",
              background=[("selected", SEL_BG)],
              foreground=[("selected", FG)])
    style.configure("Treeview.Heading", background=PANEL, foreground=FG_DIM,
                    font=FONT_UI_SM, relief="flat", bordercolor=BORDER_SOFT)
    style.map("Treeview.Heading", background=[("active", PANEL)])

    style.configure("Vertical.TScrollbar", background=TAB_IDLE, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG_FAINT, relief="flat",
                    width=px(11))
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])
    style.configure("Horizontal.TScrollbar", background=TAB_IDLE, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG_FAINT, relief="flat",
                    width=px(11))
    style.map("Horizontal.TScrollbar", background=[("active", BORDER)])

    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashrelief="flat", gripsize=px(5))

    # ------------------------------------------------------------------
    # 顶层页面选项卡：对齐参考应用「顶栏下方一排页面标签」的观感
    # ------------------------------------------------------------------
    style.configure("Page.TNotebook", background=PANEL, bordercolor=BORDER_SOFT,
                    lightcolor=PANEL, darkcolor=PANEL,
                    tabmargins=(px(8), px(7), px(0), 0))
    style.configure("Page.TNotebook.Tab", background=TAB_IDLE, foreground=FG_DIM,
                    bordercolor=BORDER_SOFT, lightcolor=TAB_IDLE,
                    darkcolor=TAB_IDLE, padding=(px(22), px(9)),
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.map("Page.TNotebook.Tab",
              background=[("selected", PANEL), ("active", HOVER)],
              foreground=[("selected", ACCENT)],
              lightcolor=[("selected", PANEL)],
              darkcolor=[("selected", PANEL)],
              expand=[("selected", (0, 0, 0, 0))])

    # ------------------------------------------------------------------
    # 卡片内的标签与控件：底色是 PANEL，不能沿用 BG 系的样式
    # ------------------------------------------------------------------
    style.configure("Card.TLabel", background=PANEL, foreground=FG, font=FONT_UI)
    style.configure("CardTitle.TLabel", background=PANEL, foreground=FG,
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("CardDim.TLabel", background=PANEL, foreground=FG_DIM,
                    font=FONT_UI_SM)
    style.configure("CardFaint.TLabel", background=PANEL, foreground=FG_FAINT,
                    font=FONT_UI_SM)
    style.configure("CardWarn.TLabel", background=PANEL, foreground=C_WARN,
                    font=FONT_UI_SM)
    style.configure("CardNum.TLabel", background=PANEL, foreground=ACCENT,
                    font=("Consolas", 10, "bold"))

    style.configure("Card.TCheckbutton", background=PANEL, foreground=FG,
                    font=FONT_UI, bordercolor=BORDER)
    style.map("Card.TCheckbutton",
              background=[("active", PANEL)],
              foreground=[("active", FG)],
              indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
              bordercolor=[("!selected", BORDER)])

    style.configure("Card.TRadiobutton", background=PANEL, foreground=FG,
                    font=FONT_UI, bordercolor=BORDER)
    style.map("Card.TRadiobutton",
              background=[("active", PANEL)],
              foreground=[("active", ACCENT)],
              indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
              bordercolor=[("!selected", BORDER)])

    # 左侧「一键配置脚本」标题条
    style.configure("ScriptTab.TLabel", background=ACCENT, foreground=ON_ACCENT,
                    font=("Microsoft YaHei UI", 10, "bold"), padding=(px(4), px(6)))

    # 每个参数区右侧的「生成脚本」按钮
    style.configure("Gen.TButton", background=GEN, foreground=ON_GEN,
                    bordercolor=GEN, focuscolor=GEN,
                    padding=(px(16), px(7)), font=FONT_UI, relief="flat")
    style.map("Gen.TButton",
              background=[("active", GEN_HOVER), ("pressed", GEN_PRESS),
                          ("disabled", TAB_IDLE)],
              bordercolor=[("active", GEN_HOVER), ("disabled", BORDER)],
              foreground=[("disabled", FG_FAINT)])

    return style
