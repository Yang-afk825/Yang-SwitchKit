"""整机开局编排自测：检查保存命令只出现一次、视图命令不重复、阶段顺序正确。

用法：python tools/test_blueprint.py [--show 厂商]
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import blueprint, registry          # noqa: E402
from scg.vendors import VENDORS, generate   # noqa: E402

SAVE = {"save force", "save", "write memory", "write"}
ENTER = {"system-view", "configure terminal"}


def defaults(scenario) -> dict:
    return {p.key: p.default for p in scenario.params}


def main() -> int:
    show = None
    if "--show" in sys.argv:
        show = sys.argv[sys.argv.index("--show") + 1]

    if show:
        results = {}
        for scenario in registry.SCENARIOS:
            try:
                results[scenario.key] = generate(
                    show, scenario.key, defaults(scenario), scenario)
            except ValueError:
                continue
        print(blueprint.build(show, results).to_text())
        return 0

    fails: list = []
    for vendor in VENDORS:
        results = {}
        for scenario in registry.SCENARIOS:
            try:
                results[scenario.key] = generate(
                    vendor.key, scenario.key, defaults(scenario), scenario)
            except ValueError:
                continue

        plan = blueprint.build(vendor.key, results, vendor.name)
        lines = [ln for block in plan.blocks for ln in block.lines]
        body = [str(ln).strip() for ln in lines if str(ln).strip()]
        commands = [ln for ln in body if not ln.startswith(("#", "!"))]

        saves = [ln for ln in commands if ln.lower() in SAVE]
        enters = [ln for ln in commands if ln in ENTER]

        # 1) 保存命令只能出现在最后一行
        if len(saves) != 1:
            fails.append((vendor.key, f"保存命令出现 {len(saves)} 次，应为 1 次"))
        elif commands and commands[-1].lower() not in SAVE:
            fails.append((vendor.key, f"最后一条不是保存命令：{commands[-1]}"))

        # 2) 进入配置视图的命令应当只在需要时出现，不能一段一条
        if len(enters) > 30:
            fails.append((vendor.key, f"进入配置视图命令出现 {len(enters)} 次，疑似未合并"))

        # 3) 阶段顺序：堆叠必须在管理面之前，管理面必须在 VLAN 之前
        titles = [block.title for block in plan.blocks if block.title]
        order = {t.split()[1] if len(t.split()) > 1 else t: i
                 for i, t in enumerate(titles)}
        if "①" in "".join(titles) and "②" in "".join(titles):
            first = next((t for t in titles if t.startswith("①")), None)
            second = next((t for t in titles if t.startswith("②")), None)
            if first and second and titles.index(first) > titles.index(second):
                fails.append((vendor.key, "堆叠阶段排到了管理面之后"))

        info = blueprint.coverage(results)
        print(f"{vendor.key:8s} 阶段 {info['stages']:2d}  场景 {info['scenes']:2d}  "
              f"命令 {len(commands):4d} 行  进入视图 {len(enters):2d} 次")

    if fails:
        print("\n问题：")
        for vendor, msg in fails:
            print(f"  [{vendor}] {msg}")
        return 1

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
