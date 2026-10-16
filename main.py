"""Yang-SwitchKit 启动入口。

双击运行时请使用打包后的 exe；源码方式运行：python main.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scg.ui import window  # noqa: E402


def main() -> None:
    window.run()


if __name__ == "__main__":
    main()
