"""打包脚本：把项目构建成单文件 Windows 可执行程序。

用法：python build.py
产物：dist/YangSwitchKit.exe
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent

#: 可执行文件名。程序里显示的名字是 Yang-SwitchKit（交换机配置命令一键生成），
#: 文件名保持纯 ASCII，避免个别环境解压或杀软处理非 ASCII 名时出问题。
NAME = "YangSwitchKit"


def check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller。请先执行：")
        print(f'  "{sys.executable}" -m pip install pyinstaller '
              f"-i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com")
        raise SystemExit(1)


def main() -> int:
    check_pyinstaller()

    # 1. 生成图标
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)

    # 2. 清理旧产物
    for name in ("build", "dist"):
        target = ROOT / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    icon = ROOT / "assets" / "app.ico"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", str(icon),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        # 这些库本项目没有用到，排除掉以缩小体积
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
        str(ROOT / "main.py"),
    ]
    print("执行：", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("打包失败。")
        return result.returncode

    exe = ROOT / "dist" / f"{NAME}.exe"
    if exe.exists():
        size = exe.stat().st_size / 1024 / 1024
        print(f"\n构建完成：{exe}")
        print(f"文件大小：{size:.1f} MB")
    else:
        print("未找到产物，请检查上面的日志。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
