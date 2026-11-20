"""Yang-SwitchKit 启动入口。

双击运行时请使用打包后的 exe；源码方式运行：python main.py

支持带方案文件启动，三种用法都能落到这里：

    YangSwitchKit.exe 方案.json     命令行
    （把文件拖到 exe 图标上）        Windows 直接转成上面这种
    （双击已关联的 .json）          同上

拖进窗口里是另一条路，见 scg/ui/dnd.py。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scg.ui import window  # noqa: E402


def _pick_path(argv) -> str:
    """从命令行参数里挑出方案文件路径。

    Windows 把拖到图标上的文件拼成 argv[1]，双击关联文件也是同一条路。
    只看第一个不是选项的参数，避免把将来可能加的 --flag 当成文件。
    """
    for arg in argv:
        if arg.startswith("-"):
            continue
        if os.path.isfile(arg):
            return os.path.abspath(arg)
    return ""


def main() -> None:
    window.run(_pick_path(sys.argv[1:]))


if __name__ == "__main__":
    main()
