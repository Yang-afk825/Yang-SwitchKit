"""一键转换测试：参数搬过去之后，还能不能正常生成命令。

    python tools/convert_test.py

不需要图形环境。测三件事：

1. 端口编号换算的各种写法（含不该被误伤的 IP、VLAN、时间）
2. 参数搬运只动端口，别的一律原样
3. 端到端：四家两两转换后用目标厂商真跑一遍 ``generate``，不许抛异常
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import convert, registry              # noqa: E402
from scg.vendors import VENDORS, generate, supports   # noqa: E402

FAILS: list[str] = []

#: 端到端样本：二层、聚合、三层、安全各一个，覆盖面够
SAMPLE = ("vlan_port", "lag", "ospf", "port_security")


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}" + (f"　{detail}" if detail else ""))
        FAILS.append(name)


def test_ports() -> None:
    print("\n[1] 端口编号换算")
    cases = [
        # (输入, 源, 目标, 期望)
        ("1/0/1", "h3c", "huawei", "0/0/1"),        # 三段对三段，首段 -1
        ("0/0/1", "huawei", "h3c", "1/0/1"),        # 反向 +1
        ("2/0/5", "h3c", "huawei", "1/0/5"),        # 堆叠第二台
        ("1/0/1-8", "h3c", "huawei", "0/0/1-8"),    # 范围只翻首号，末号不动
        ("1/0/1-1/0/8", "h3c", "huawei", "0/0/1-0/0/8"),
        ("1/0/1,1/0/3", "h3c", "huawei", "0/0/1,0/0/3"),
        ("1/0/1", "h3c", "cisco", "0/1"),           # 三段压两段
        ("0/1", "cisco", "h3c", "1/0/1"),           # 两段补三段
        ("XGE1/0/49", "h3c", "huawei", "XGE0/0/49"),  # 别名原样保留
        ("1/0/24", "h3c", "ruijie", "0/24"),
        ("0/1", "cisco", "ruijie", "0/1"),          # 同构直搬
        ("1/1", "h3c", "huawei", "0/0/1"),          # 两段简写也能归一
    ]
    for value, src, dst, want in cases:
        got = convert.convert_value(value, src, dst, [])
        check(f"{src} {value} → {dst} {want}", got == want, f"实际 {got}")

    print("\n[2] 不该被误伤的值")
    for value in ("192.168.1.1/24", "10.0.0.1 255.255.255.0", "10,20,30-32",
                  "access", "auto", "Vlan-interface 10",
                  "UTC add 08:00:00", "0/0"):
        got = convert.convert_value(value, "h3c", "huawei", [])
        check(f"{value} 原样保留", got == value, f"实际 {got}")


def test_params() -> None:
    print("\n[3] 参数搬运：只动端口")
    scenario = registry.get("vlan_port")
    params = {p.key: p.default for p in scenario.params}
    params["ports"] = "1/0/1-8"
    params["vlans"] = "10,20,30"
    params["gateway"] = "192.168.1.254"

    new, report = convert.convert_params(params, scenario, "h3c", "huawei")
    check("端口被换算", new["ports"] == "0/0/1-8", f"实际 {new['ports']}")
    check("VLAN 原样", new["vlans"] == params["vlans"], f"实际 {new['vlans']}")
    check("网关原样", new["gateway"] == params["gateway"], f"实际 {new['gateway']}")
    check("换算是就地改名不是改对象", params["ports"] == "1/0/1-8")
    check("换算记录非空", bool(report))

    same, empty = convert.convert_params(params, scenario, "h3c", "h3c")
    check("同厂商转换是空操作", same == params and empty == [])

    # 空值不能炸
    blank = {p.key: p.default for p in scenario.params}
    blank["ports"] = ""
    try:
        convert.convert_params(blank, scenario, "h3c", "huawei")
        check("端口留空不报错", True)
    except Exception as exc:  # noqa: BLE001
        check("端口留空不报错", False, str(exc))


def test_end_to_end() -> None:
    print("\n[4] 端到端：转换后用目标厂商真跑一遍")
    keys = [v.key for v in VENDORS]
    for scenario_key in SAMPLE:
        scenario = registry.get(scenario_key)
        for src in keys:
            if not supports(src, scenario_key):
                continue
            params = {p.key: p.default for p in scenario.params}
            for dst in keys:
                if dst == src or not supports(dst, scenario_key):
                    continue
                new, _ = convert.convert_params(params, scenario, src, dst)
                try:
                    result = generate(dst, scenario_key, new, scenario)
                    cond = bool(result.blocks)
                    detail = "没有任何输出"
                except Exception as exc:  # noqa: BLE001
                    cond = False
                    detail = f"{type(exc).__name__}: {exc}"
                check(f"{scenario_key}: {src} → {dst}", cond, detail)


def main() -> int:
    test_ports()
    test_params()
    test_end_to_end()

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for name in FAILS:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
