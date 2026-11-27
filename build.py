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


def version_tuple(version: str) -> str:
    """'3.0.0' -> '(3, 0, 0, 0)'，PyInstaller 的版本资源要四段数字。"""
    parts = (version.split(".") + ["0", "0", "0", "0"])[:4]
    return "(" + ", ".join(str(int(p)) if p.isdigit() else "0" for p in parts) + ")"


def write_version_resource() -> pathlib.Path:
    """生成 exe 的版本资源。

    没有这个文件，exe 右键「属性 → 详细信息」里的版本一栏是空的 —— 用户拿到
    一个 exe，看不出它是哪一版、什么时候的。版本号统一从 scg.__version__ 取，
    保证「界面里显示的」和「文件属性里的」永远是同一个数。
    """
    sys.path.insert(0, str(ROOT))
    from scg import __version__

    nums = version_tuple(__version__)
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404B0', [
      StringStruct('CompanyName', '小阳的赛博日记'),
      StringStruct('FileDescription', '交换机配置命令一键生成'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', '公众号：小阳的赛博日记'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'Yang-SwitchKit'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('Comments', '思科 / 华为 / H3C / 锐捷 四厂商交换机配置生成与变更核对')
    ])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""", encoding="utf-8")
    return path


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

    # 3. 版本资源：让 exe 属性里能看到版本号
    version_file = write_version_resource()

    icon = ROOT / "assets" / "app.ico"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", str(icon),
        "--version-file", str(version_file),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        # AI 模块是「点了才 import」的，虽然 PyInstaller 的字节码分析能跟上
        # 函数体内的 import，这里还是显式写出来：少一次「打出来的 exe
        # 在别的机器上点 AI 页签就崩」的排查
        "--hidden-import", "scg.ai.config",
        "--hidden-import", "scg.ai.provider",
        "--hidden-import", "scg.ai.schema",
        "--hidden-import", "scg.ai.rules",
        "--hidden-import", "scg.ai.redact",
        "--hidden-import", "scg.ai.intent",
        "--hidden-import", "scg.ai.advise",
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
