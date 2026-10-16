"""全组合冒烟测试：4 个厂商 × 10 个场景，用默认参数各生成一遍。

用法：python tools/smoke_test.py [--show 厂商/场景]
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import registry                      # noqa: E402
from scg.vendors import VENDORS, generate     # noqa: E402


def defaults(scenario) -> dict:
    return {p.key: p.default for p in scenario.params}


def main() -> int:
    show = None
    if "--show" in sys.argv:
        show = sys.argv[sys.argv.index("--show") + 1]

    total = 0
    fails: list[tuple] = []
    for vendor in VENDORS:
        for scenario in registry.SCENARIOS:
            total += 1
            try:
                result = generate(vendor.key, scenario.key, defaults(scenario), scenario)
                text = result.to_text()
                if not text.strip():
                    raise AssertionError("生成为空")
                if not result.blocks:
                    raise AssertionError("没有任何命令区块")
            except Exception as exc:  # noqa: BLE001
                fails.append((vendor.name, scenario.name, type(exc).__name__, str(exc)))

    print(f"厂商 {len(VENDORS)} × 场景 {len(registry.SCENARIOS)} = 组合 {total}")
    if fails:
        print(f"失败 {len(fails)} 项：")
        for f in fails:
            print(f"  [{f[0]}] {f[1]}  ->  {f[2]}: {f[3]}")
        return 1

    print("全部通过")

    if show:
        vendor_key, _, scenario_key = show.partition("/")
        scenario = registry.get(scenario_key)
        result = generate(vendor_key, scenario_key, defaults(scenario), scenario)
        print()
        print("=" * 72)
        print(result.to_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
