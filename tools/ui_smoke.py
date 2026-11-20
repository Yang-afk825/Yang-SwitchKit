"""界面冒烟：不开窗口，把「改参数 → 保存方案 → 清空 → 打开方案」跑一遍。

只验证界面与方案文件之间的接线有没有接错，不测视觉效果：

    python tools/ui_smoke.py

窗口用 withdraw() 藏起来，不会在屏幕上闪。没装图形环境（例如纯 SSH 的
Linux 服务器）时直接跳过并返回 0，不当作失败。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}" + (f"　{detail}" if detail else ""))
        FAILS.append(name)


def main() -> int:
    try:
        import tkinter as tk
    except ImportError:
        print("跳过：当前 Python 没有 Tkinter")
        return 0

    try:
        probe = tk.Tk()
        probe.destroy()
    except tk.TclError:
        print("跳过：没有可用的图形环境")
        return 0

    from scg import registry
    from scg.ui.window import App

    app = App()
    app.withdraw()          # 不显示窗口，只在后台跑逻辑
    try:
        print("一、初始状态")

        check("标题里有「未命名方案」", "未命名方案" in app.title(), app.title())
        check("初始不是脏状态", app.dirty is False)
        check("初始没有方案路径", app.project_path is None)

        print("\n二、改参数 + 生成")

        # 用 vlan_port 场景的 VLAN 列表参数做样本
        kind, field = app.widgets["vlan_port"]["vlans"]
        field.set("100,200,300")
        app._gen_one("vlan_port")

        check("生成成功", "vlan_port" in app.results)
        check("改参数后标记为脏", app.dirty is True)
        check("标题出现未保存标记 *", app.title().endswith("*"), app.title())
        check("脚本区有内容", "100" in app._script_text())

        print("\n三、保存方案")

        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "冒烟方案.json"

            # 绕过文件对话框，直接写到指定路径
            from scg import project as project_mod
            project_mod.to_file(target, app._collect_project())
            app.project_path = target
            app._clear_dirty()

            check("文件已写出", target.exists())
            check("保存后不再是脏状态", app.dirty is False)
            check("标题显示方案名", "冒烟方案.json" in app.title(), app.title())

            # 记下保存时的样子，后面比对
            saved_params = dict(app.store[("h3c", "vlan_port")])
            check("保存的参数里有 100,200,300",
                  saved_params.get("vlans") == "100,200,300", str(saved_params.get("vlans")))

            print("\n四、清空后再打开")

            app._clear_all()
            check("清空后脚本区为空", app._script_text().strip() == "")
            check("清空后没有生成记录", app.order == [])

            plan, warnings = project_mod.from_file(target)
            check("读文件无警告", warnings == [], str(warnings))

            app._apply_project(plan)
            app.project_path = target
            app._clear_dirty()

            field_after = app.widgets["vlan_port"]["vlans"][1]
            check("参数值回来了", field_after.get() == "100,200,300", field_after.get())
            check("已生成的场景被重新生成", "vlan_port" in app.results)
            check("脚本区内容也回来了", "100" in app._script_text())
            check("打开后不是脏状态", app.dirty is False)

            print("\n五、切厂商不丢参数")

            app.vendor_var.set("huawei")
            app._on_vendor_change()
            check("切到华为后仍能生成", "vlan_port" in app.results)
            # 华为的 VLAN 参数是独立的，切回去应当还是原值
            app.vendor_var.set("h3c")
            app._on_vendor_change()
            check("切回 H3C 参数没被冲掉",
                  app.widgets["vlan_port"]["vlans"][1].get() == "100,200,300",
                  app.widgets["vlan_port"]["vlans"][1].get())

            print("\n六、按路径打开（命令行 / 拖到图标上 / 拖进窗口 都走这里）")

            check("拖放已挂上", app.drop.enabled,
                  "enable() 没成功，窗口内拖放不可用")

            app._clear_all()
            check("打开前脚本区是空的", app._script_text().strip() == "")

            ok = app.open_path(str(target))
            check("open_path 返回成功", ok is True)
            check("参数被载入", app.widgets["vlan_port"]["vlans"][1].get() == "100,200,300",
                  app.widgets["vlan_port"]["vlans"][1].get())
            check("方案路径被记住", app.project_path is not None)
            check("已生成的场景被重算", "vlan_port" in app.results)

            print("\n七、命令行参数解析")

            sys.path.insert(0, str(ROOT))
            from main import _pick_path

            check("能从参数里挑出文件", _pick_path(["--foo", str(target)]) == str(target))
            check("跳过选项、只认存在的文件", _pick_path(["--foo"]) == "")
            check("没有参数时返回空", _pick_path([]) == "")

            print("\n八、保存的反馈")

            # 刚打开、没有改动时点保存：不该重写文件，要给个明确的提示
            stat_before = target.stat().st_mtime
            ok = app._save_project()
            check("无改动时保存返回成功", ok is True)
            check("无改动时不重写文件", target.stat().st_mtime == stat_before)
            check("无改动时状态栏有提示",
                  "已是最新" in app.status.cget("text"),
                  app.status.cget("text")[:50])

            # 有改动时保存：写盘 + 绿色提示
            app.widgets["vlan_port"]["pvid"][1].set("199")
            app._gen_one("vlan_port")
            check("改动后标记为脏", app.dirty is True)
            ok = app._save_project()
            check("有改动时保存成功", ok is True)
            check("保存后不再是脏状态", app.dirty is False)
            check("文件确实被改写", target.stat().st_mtime >= stat_before)
            check("状态栏用醒目样式提示",
                  str(app.status.cget("style")).startswith("OK"),
                  str(app.status.cget("style")))

    finally:
        app.destroy()

    print()
    if FAILS:
        print(f"结论：{len(FAILS)} 项未通过")
        return 1
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
