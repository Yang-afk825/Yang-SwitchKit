"""回滚引擎自测：对全部 116 组生成结果推导回滚，统计覆盖情况并抽样打印。

用法：python tools/test_rollback.py [--show 厂商/场景]
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import registry, rollback          # noqa: E402
from scg.model import Kind                  # noqa: E402
from scg.vendors import VENDORS, generate   # noqa: E402


def variants(scenario):
    base = {p.key: p.default for p in scenario.params}
    yield dict(base)
    for p in scenario.params:
        if p.kind == Kind.CHOICE:
            for value, _label in p.choices:
                if str(value) == str(base.get(p.key)):
                    continue
                data = dict(base)
                data[p.key] = value
                yield data
        elif p.kind == Kind.BOOL:
            for flag in ("1", ""):
                data = dict(base)
                data[p.key] = flag
                yield data


def defaults(scenario) -> dict:
    return {p.key: p.default for p in scenario.params}


def unknown_of(vendor_key: str, result) -> list:
    """直接问回滚引擎：这段配置里有哪些命令无法自动撤销。"""
    rules = rollback._compile_table(vendor_key)
    lines = [ln for b in result.blocks for ln in b.lines]
    tree = rollback._parse(lines, vendor_key, rules)

    found: list = []

    def walk(nodes):
        for node in nodes:
            if isinstance(node, rollback._Cmd):
                if node.rule.conf == "manual":
                    found.append(node.text)
            else:
                walk(node.children)

    walk(tree)
    return found


def main() -> int:
    show = None
    if "--show" in sys.argv:
        show = sys.argv[sys.argv.index("--show") + 1]

    if show:
        vendor_key, _, scenario_key = show.partition("/")
        scenario = registry.get(scenario_key)
        result = generate(vendor_key, scenario_key, defaults(scenario), scenario)
        undo = rollback.build(vendor_key, result)
        print(undo.to_text())
        return 0

    total = 0
    empty = []
    fails = []
    manual_total = 0
    approx_total = 0
    unknown_cmds: dict = {}

    for vendor in VENDORS:
        for scenario in registry.SCENARIOS:
            for data in variants(scenario):
                total += 1
                try:
                    result = generate(vendor.key, scenario.key, data, scenario)
                except ValueError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    fails.append((vendor.key, scenario.key, repr(exc)))
                    continue
                try:
                    undo = rollback.build(vendor.key, result)
                except Exception as exc:  # noqa: BLE001
                    fails.append((vendor.key, scenario.key, repr(exc)))
                    continue

                lines = [ln for b in undo.blocks for ln in b.lines]
                body = [ln for ln in lines
                        if ln.strip() and not ln.strip().startswith("#")]
                # 只剩 system-view / return / save 说明一条 undo 都没推出来
                if len(body) <= 3:
                    empty.append(f"{vendor.key}/{scenario.key}")
                manual_total += sum(1 for ln in lines if ln.strip().startswith("# [人工确认]"))
                approx_total += sum(1 for ln in lines if ln.strip().startswith("# [近似]"))

                for cmd in unknown_of(vendor.key, result):
                    unknown_cmds.setdefault(cmd, set()).add(f"{vendor.key}/{scenario.key}")

    print(f"组合 {total} 组")
    print(f"未推导出 undo：{len(set(empty))} 个场景 -> {sorted(set(empty))}")
    print(f"人工确认行 {manual_total} 条，近似行 {approx_total} 条")
    if fails:
        print(f"异常 {len(fails)} 项：")
        for f in fails[:10]:
            print("   ", f)
    if unknown_cmds:
        print(f"未识别命令 {len(unknown_cmds)} 种：")
        for cmd, where in sorted(unknown_cmds.items()):
            print(f"    {cmd:70s} {sorted(where)[:3]}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
