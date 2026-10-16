"""临时脚本：启动界面并截图，用于人工核对视觉效果。

用 PrintWindow 抓窗口自身画面，不会把压在上面的其它窗口一起拍进来。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.wincap import grab_window  # noqa: E402

from scg.ui.window import App  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_preview")
os.makedirs(OUT, exist_ok=True)


def shot(app, name: str) -> None:
    # 主题切换、翻页之后要留一点时间让窗口把新画面画出来，
    # 只调一次 update() 抓到的可能还是上一帧。
    for delay in (0.0, 0.12, 0.3):
        if delay:
            time.sleep(delay)
        app.update_idletasks()
        app.update()
    img = grab_window(app)
    path = os.path.join(OUT, name)
    img.save(path)
    print("saved", os.path.abspath(path), img.size, flush=True)


def main() -> None:
    app = App()
    app.update()
    app._gen_all()
    app.update()
    shot(app, "01_main_ruijie.png")

    app.vendor_var.set("h3c")
    app._on_vendor_change()
    app.update()
    shot(app, "02_main_h3c.png")

    app.pages.select(app.page_doc)
    app.update()
    shot(app, "03_doc.png")

    app._toggle_theme()
    app.pages.select(app.page_main)
    app.update()
    shot(app, "04_dark.png")

    app.destroy()
    os._exit(0)


if __name__ == "__main__":
    main()
