"""把方案文件拖进窗口就能打开。

为什么不用现成库
----------------
Tkinter 自己不处理文件拖放。常见做法是装 TkDND，但那个要额外带一个 DLL，
本项目约定「只用标准库、绿色单文件」，不能为了一个拖放破例。

所以直接跟 Win32 打交道，三步：

1. ``DragAcceptFiles(hwnd, True)`` 告诉系统这个窗口接收文件
2. 用户松手时系统往窗口过程发 ``WM_DROPFILES``
3. Tk 不暴露窗口过程，于是用 ``SetWindowLongPtr`` 把它换掉，
   自己处理 ``WM_DROPFILES``，其余消息原样转给原来的过程

风险控制
--------
子类化窗口过程属于「动系统底层」，写错了会让窗口失去响应，所以：

* 回调对象挂在实例上长期持有，被垃圾回收会导致进程直接崩
* ``enable()`` 全程 try/except，失败只返回 False，程序照常运行，
  只是拖放不可用（按钮和命令行仍然能打开文件）
* 取到路径后不在窗口过程里直接干活，扔回 Tk 的事件队列再处理，
  因为加载方案会重建整个界面，放在消息处理里太危险
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

#: 窗口过程签名。返回值在 64 位下是 8 字节，用 ssize_t 才对得上，
#: 写成 c_long 在 64 位 Windows 上会把返回值截断
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

# 32 位系统没有 SetWindowLongPtrW，只能退回 SetWindowLongW
_set_window_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_set_window_long.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
_set_window_long.restype = ctypes.c_void_p

user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT,
                                   wt.WPARAM, wt.LPARAM]
user32.CallWindowProcW.restype = ctypes.c_ssize_t

user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND

shell32.DragAcceptFiles.argtypes = [wt.HWND, wt.BOOL]
shell32.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
shell32.DragQueryFileW.restype = wt.UINT
shell32.DragFinish.argtypes = [wt.HANDLE]


def top_hwnd(widget) -> int:
    """Tk 控件的 winfo_id() 是子窗口，要一路往上找到顶层窗口句柄。"""
    hwnd = int(widget.winfo_id())
    parent = user32.GetParent(hwnd)
    while parent:
        hwnd, parent = parent, user32.GetParent(parent)
    return hwnd


class FileDrop:
    """给一个 Tk 顶层窗口挂上接收文件拖放的能力。

    用法::

        drop = FileDrop(app, lambda paths: print(paths))
        if drop.enable():
            ...
    """

    def __init__(self, widget, on_drop) -> None:
        self._widget = widget
        self._on_drop = on_drop
        self._hwnd = 0
        self._old_proc = None
        self._proc = None        # 必须持有引用，否则回调被回收就崩
        self._enabled = False

    @property
    def enabled(self) -> bool:
        """拖放是否已经挂上。"""
        return self._enabled

    def enable(self) -> bool:
        """挂上拖放。返回是否成功，失败不影响程序其余部分。"""
        if self._enabled:
            return True
        try:
            self._hwnd = top_hwnd(self._widget)
            self._proc = WNDPROC(self._handle)
            old = _set_window_long(self._hwnd, GWLP_WNDPROC,
                                  ctypes.cast(self._proc, ctypes.c_void_p))
            if not old:
                return False
            self._old_proc = old
            shell32.DragAcceptFiles(self._hwnd, True)
            self._enabled = True
        except Exception:  # noqa: BLE001 —— 拖放不是核心功能，坏了就不启用
            self._enabled = False
        return self._enabled

    def disable(self) -> None:
        """把窗口过程还回去。窗口销毁前调用，避免留下野指针。"""
        if not self._enabled:
            return
        try:
            _set_window_long(self._hwnd, GWLP_WNDPROC, self._old_proc)
            shell32.DragAcceptFiles(self._hwnd, False)
        except Exception:  # noqa: BLE001
            pass
        self._enabled = False
        self._old_proc = None
        self._proc = None

    # ------------------------------------------------------------------

    def _handle(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            try:
                paths = self._read_paths(wparam)
            except Exception:  # noqa: BLE001
                paths = []
            if paths:
                # 不在消息处理里直接加载方案：那会重建整个界面，
                # 扔回 Tk 的事件队列，等这一轮消息处理完再干
                self._widget.after_idle(lambda: self._on_drop(paths))
            return 0
        return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    @staticmethod
    def _read_paths(hdrop) -> list:
        """从 HDROP 里取出拖进来的文件路径，然后释放它。"""
        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        paths: list = []
        for index in range(min(int(count), 8)):     # 最多取 8 个，够用了
            length = shell32.DragQueryFileW(hdrop, index, None, 0)
            if length <= 0:
                continue
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(hdrop, index, buf, length + 1)
            paths.append(buf.value)
        shell32.DragFinish(hdrop)
        return paths
