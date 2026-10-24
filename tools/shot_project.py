"""给 2.1 的方案保存功能拍一套说明图。

拍的是**程序真实运行时的窗口画面**（PrintWindow 重绘，不是截图工具拍的屏幕，
也不是画出来的示意图）：

    10_主界面.png     填好参数、已保存，状态栏显示方案名
    11_顶栏按钮.png   顶栏「打开方案 / 保存方案」特写，红框标出
    12_状态栏.png     状态栏特写，能看到「方案：xxx.json」
    13_方案文件.png   用系统记事本打开方案 json 的真实画面

用法：python tools/shot_project.py
产物在 _preview/ 下（该目录已被 .gitignore 排除）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from tools.wincap import find_window, grab_hwnd, grab_window, top_level_hwnd  # noqa: E402

from scg import project  # noqa: E402
from scg.ui.window import App  # noqa: E402

user32 = ctypes.windll.user32

#: 本机的 Notepad++。系统自带记事本在这台机器上起不来（WinError 14001）
NOTEPADPP = pathlib.Path(r"D:\Notepad++\Notepad++\notepad++.exe")

OUT = ROOT / "_preview"
OUT.mkdir(exist_ok=True)

FONT = "C:/Windows/Fonts/msyhbd.ttc"
RED = "#C0392B"


def font(size: int):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def settle(app, rounds: int = 3) -> None:
    """让 Tk 把待处理的绘制都做完，否则抓到的可能是上一帧。"""
    for i in range(rounds):
        if i:
            time.sleep(0.1)
        app.update_idletasks()
        app.update()


def set_param(app, scene: str, key: str, value: str) -> None:
    """按参数类型塞值。界面把参数分成 text / choice / bool / var 四类。"""
    kind, widget = app.widgets[scene][key]
    if kind == "text":
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
    elif kind == "choice":
        for label, val in app.choice_maps[scene][key].items():
            if val == value:
                widget.set(label)
                return
    else:
        widget.set(value)


def save(img: Image.Image, name: str, width: int | None = None) -> pathlib.Path:
    if width and img.width != width:
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)), Image.LANCZOS)
    path = OUT / name
    img.save(path)
    print(f"  {name}  {img.size[0]}×{img.size[1]}")
    return path


def widget_box(app, widget) -> tuple:
    """控件在窗口客户区里的像素位置。"""
    return (widget.winfo_rootx() - app.winfo_rootx(),
            widget.winfo_rooty() - app.winfo_rooty(),
            widget.winfo_width(), widget.winfo_height())


def trim_right(img: Image.Image, tolerance: int = 18) -> Image.Image:
    """按背景色裁掉右侧空白，状态栏那种「一整条但只有左边有字」的图用得上。"""
    px = img.load()
    bg = px[img.width - 1, img.height // 2]
    right = img.width
    for x in range(img.width - 1, 0, -1):
        hit = False
        for y in range(0, img.height, 2):
            r, g, b = px[x, y][:3]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > tolerance:
                hit = True
                break
        if hit:
            right = min(img.width, x + 12)
            break
    return img.crop((0, 0, right, img.height))


def main() -> int:
    app = App()
    app.update()

    # ---- 填一份看起来像真事儿的参数 ----------------------------------
    # VLAN 与端口数量按工具的规则一一对应（4 个 VLAN 对 4 个口），
    # 否则会触发「端口数不匹配」的风险提示，截图里看着像是工具有问题
    set_param(app, "vlan_port", "vlans", "10,20,30,40")
    set_param(app, "vlan_port", "vlan_names", "10:研发,20:测试,30:办公,40:管理")
    set_param(app, "vlan_port", "ports", "1/0/1-4")
    set_param(app, "vlan_port", "pvid", "99")

    set_param(app, "lag", "ports", "1/0/23-24")
    set_param(app, "lag", "allowed", "10,20,30,40")

    set_param(app, "ospf", "router_id", "1.1.1.1")
    set_param(app, "ospf", "networks", "192.168.10.0/24 area 0\n192.168.20.0/24 area 0")

    for key in ("vlan_port", "lag", "ospf"):
        app._gen_one(key)
    settle(app)

    # ---- 存方案，让状态栏显示文件名 ----------------------------------
    target = OUT / "示例方案.json"
    project.to_file(target, app._collect_project())
    app.project_path = target
    app._clear_dirty()

    # 「已生成…」那类临时提示 5 秒后才退回常驻信息，截图等不起，
    # 直接把定时器取消掉，立刻刷成常驻状态
    if app._status_timer:
        app.after_cancel(app._status_timer)
        app._status_timer = None
    app._set_status()
    settle(app)

    print("一、程序界面")

    full = grab_window(app)
    save(full, "10_主界面.png", width=1600)

    # 顶栏两个新按钮特写：坐标从控件实时取，不写死
    import tkinter.ttk as ttk

    wanted = {}
    for w in app._walk(widget_class=ttk.Button):
        try:
            label = w.cget("text")
        except Exception:  # noqa: BLE001
            continue
        if label in ("打开方案", "保存方案"):
            wanted[label] = w

    if len(wanted) == 2:
        boxes = [widget_box(app, w) for w in wanted.values()]
        x0 = min(b[0] for b in boxes) - 16
        x1 = max(b[0] + b[2] for b in boxes) + 16
        y0 = min(b[1] for b in boxes) - 12
        y1 = max(b[1] + b[3] for b in boxes) + 12
        crop = full.crop((x0, y0, x1, y1))
        scale = 3
        big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)

        pad_top = 56
        canvas = Image.new("RGB", (big.width, big.height + pad_top), "#FFF8F8")
        canvas.paste(big, (0, pad_top))
        d = ImageDraw.Draw(canvas)
        d.text((14, 12), "2.1 新增", fill=RED, font=font(30))
        for b in boxes:
            left = (b[0] - x0) * scale
            right = (b[0] + b[2] - x0) * scale
            top = pad_top + (b[1] - y0) * scale
            bottom = pad_top + (b[1] + b[3] - y0) * scale
            d.rectangle([left - 4, top - 4, right + 4, bottom + 4],
                        outline=RED, width=4)
        save(canvas, "11_顶栏按钮.png")
    else:
        print("  ! 没找到顶栏按钮，跳过特写")

    # 标题栏特写：grab_window 只给客户区，要看窗口标题得抓整窗再裁
    hwnd = top_level_hwnd(app)
    win_rect = wt.RECT()
    cli_rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(win_rect))
    user32.GetClientRect(hwnd, ctypes.byref(cli_rect))
    origin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    title_h = max(1, int(origin.y - win_rect.top))

    titled = grab_hwnd(hwnd, client_only=False)
    bar = titled.crop((0, 0, int(titled.width * 0.26), title_h + 2))
    bar = trim_right(bar)
    bar = bar.resize((bar.width * 2, bar.height * 2), Image.LANCZOS)
    save(bar, "12_标题栏.png")

    app.destroy()

    # ---- 用 Notepad++ 打开方案文件，拍真实画面 ----------------------
    print("二、方案文件")
    if not NOTEPADPP.exists():
        print(f"  ! 没找到 {NOTEPADPP}，跳过")
        return 0

    # -multiInst 起独立实例，不会挤进用户已经开着的那个窗口里
    subprocess.Popen([str(NOTEPADPP), "-multiInst", "-nosession", str(target)])
    hwnd = find_window(target.name, timeout=15)
    if not hwnd:
        print("  ! 没等到 Notepad++ 窗口，跳过")
        return 0

    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    # 摆成合适大小，默认窗口太小，json 的前几行就看不全了
    user32.SetWindowPos(hwnd, 0, 150, 120, 900, 800, 0x0004)
    time.sleep(1.2)

    img = grab_hwnd(hwnd)
    save(img, "13_方案文件.png", width=900)

    user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE，只关这一个实例
    print("\n完成，产物在", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
