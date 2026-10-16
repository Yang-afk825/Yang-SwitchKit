"""Windows 高 DPI 适配。

不处理 DPI 感知的 Tk 程序在 125% / 150% 缩放下会被系统位图拉伸，界面发虚。
这里声明 DPI 感知并按缩放比例放大间距，字体由 Tk 的 scaling 自动适配。
"""
from __future__ import annotations

import sys

_enabled = False


def enable_awareness() -> None:
    """声明进程 DPI 感知。必须在创建 Tk 窗口之前调用。"""
    global _enabled
    if _enabled or not sys.platform.startswith("win"):
        return
    try:
        # 1 = PROCESS_SYSTEM_DPI_AWARE，Win8.1+
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        _enabled = True
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        _enabled = True
    except Exception:  # noqa: BLE001
        pass


def scale_for(root) -> float:
    """取得当前屏幕的缩放系数（96 DPI 记为 1.0），并把 Tk 的字号换算也调过去。"""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        return 1.0
    if dpi <= 0:
        return 1.0
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:  # noqa: BLE001
        pass
    return max(1.0, min(3.0, dpi / 96.0))
