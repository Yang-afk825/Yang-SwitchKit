"""只抓取指定窗口自身的内容，不会把桌面上的其它窗口拍进来。

原理：用 Win32 的 ``PrintWindow(PW_RENDERFULLCONTENT)`` 让目标窗口把自己
重绘到一块内存 DC，再取出位图。因此即使该窗口被别的程序遮挡（弹窗、通知、
聊天窗口），抓到的仍然是它自己的画面。

这一点很重要：直接截屏（ImageGrab）会把当时压在窗口上面的任何东西一起拍下来，
包括别人的私人聊天窗口。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002

# 64 位下句柄相关的出入参类型必须显式声明，否则句柄会被当成 int 截断或溢出
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.restype = wt.BOOL
user32.PrintWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_uint]
user32.PrintWindow.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int


class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wt.DWORD * 3)]


def top_level_hwnd(widget) -> int:
    """拿到 Tk 顶层窗口真正的 HWND。winfo_id() 返回的是它的子窗口。"""
    hwnd = int(widget.winfo_id())
    parent = user32.GetParent(hwnd)
    while parent:
        hwnd, parent = parent, user32.GetParent(parent)
    return hwnd


def grab_window(widget) -> Image.Image:
    """抓取该控件所属顶层窗口当前的**客户区**画面。"""
    widget.update_idletasks()
    return grab_hwnd(top_level_hwnd(widget))


def grab_hwnd(hwnd: int, client_only: bool = True) -> Image.Image:
    """抓任意窗口句柄的画面。

    ``PrintWindow`` 是整个窗口（含标题栏）往 DC 上画的。
    ``client_only=True``（默认）时按客户区在窗口里的偏移裁掉标题栏和边框，
    否则图片会整体下移，底部那一条（状态栏）就被切掉了。

    ``client_only=False`` 保留标题栏，用来展示窗口标题本身。

    抓本程序自己的窗口用 ``grab_window(widget)``；
    抓外部程序时直接传 HWND 进来，句柄可以用 :func:`find_window` 按标题找。
    """
    client = wt.RECT()
    window = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    user32.GetWindowRect(hwnd, ctypes.byref(window))
    cw, ch = int(client.right), int(client.bottom)
    ww, wh = int(window.right - window.left), int(window.bottom - window.top)
    if cw <= 0 or ch <= 0 or ww <= 0 or wh <= 0:
        raise RuntimeError("窗口尺寸无效")

    origin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    off_x = max(0, int(origin.x - window.left))
    off_y = max(0, int(origin.y - window.top))

    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, ww, wh)
    old = gdi32.SelectObject(mem, bmp)
    try:
        user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
        info = _BMI()
        info.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        info.bmiHeader.biWidth = ww
        info.bmiHeader.biHeight = -wh        # 负值 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0     # BI_RGB
        buf = ctypes.create_string_buffer(ww * wh * 4)
        gdi32.GetDIBits(mem, bmp, 0, wh, buf, ctypes.byref(info), 0)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)

    img = Image.frombuffer("RGBA", (ww, wh), buf, "raw", "BGRA", 0, 1)
    if client_only:
        img = img.crop((off_x, off_y, off_x + cw, off_y + ch))
    return img.convert("RGB")


def find_window(title_part: str, timeout: float = 8.0) -> int:
    """按标题片段找一个可见的顶层窗口，返回 HWND；超时返回 0。

    抓外部程序窗口时用得上：程序启动后不一定马上把窗口建好，
    而且 PyInstaller 产物是「引导进程 + 应用进程」两层，窗口出现得晚。
    """
    import time

    found: list = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_part in buf.value:
            found.append(hwnd)
            return False
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        found.clear()
        user32.EnumWindows(_callback, 0)
        if found:
            return int(found[0])
        time.sleep(0.2)
    return 0
