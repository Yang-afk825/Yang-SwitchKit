"""精确回滚（基线模式）自测。

重点验证四件事：
  1. 没有基线时行为不变（仍是 undo + 近似提示）
  2. 有基线时覆盖型命令恢复原值
  3. interface range 里各端口原配置不同时，拆开各自恢复
  4. 基线里本来就存在的 VLAN / 接口，不能整块删掉

用法：python tools/test_baseline.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import registry, rollback          # noqa: E402
from scg.vendors import generate            # noqa: E402


def lines_of(result) -> list:
    return [str(ln) for block in result.blocks for ln in block.lines]


def run(vendor, scenario_key, params, baseline_text=None):
    scenario = registry.get(scenario_key)
    res = generate(vendor, scenario_key, params, scenario)
    base = (rollback.Baseline.parse(baseline_text, vendor)
            if baseline_text else None)
    return rollback.build(vendor, res, baseline=base), base


def main() -> int:
    fails: list = []

    def check(name, cond, detail=""):
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}  {detail}")
            fails.append(name)

    params = {
        "vlans": "10,20", "vlan_names": "", "port_type": "trunk",
        "ports": "1/0/1-3", "allowed": "10,20", "pvid": "99",
    }

    print("\n[1] 无基线：行为不变")
    out, _ = run("h3c", "vlan_port", params)
    body = lines_of(out)
    check("仍然是 undo port link-type",
          any("undo port link-type" in ln for ln in body))
    check("仍然标近似",
          any("[近似]" in ln for ln in body))

    print("\n[2] 单接口基线：恢复原值")
    base_text = """#
 interface GigabitEthernet1/0/1
 port link-type access
 port access vlan 10
#
"""
    # 只给一个端口的基线，所以生成也只取这个端口 —— 否则 range 里另外两个
    # 端口查不到原值，按设计要整条放弃（见用例 3 的拆分逻辑）
    single = dict(params)
    single["ports"] = "1/0/1"
    out, base = run("h3c", "vlan_port", single, base_text)
    body = [ln.strip() for ln in lines_of(out)]
    check("基线解析到条目", len(base) >= 2, f"len={len(base)}")
    check("恢复成 access", "port link-type access" in body)
    check("不再出现 undo port link-type", "undo port link-type" not in body)
    check("不再标近似", not any("[近似]" in ln for ln in lines_of(out)))

    print("\n[3] range 内各端口原配置不同：拆开分别恢复")
    base_text = """#
 interface GigabitEthernet1/0/1
 port link-type access
#
 interface GigabitEthernet1/0/2
 port link-type hybrid
#
 interface GigabitEthernet1/0/3
 port link-type access
#
"""
    out, _ = run("h3c", "vlan_port", params, base_text)
    body = [ln.strip() for ln in lines_of(out)]
    check("出现逐个接口块",
          "interface GigabitEthernet1/0/2" in body)
    check("hybrid 端口恢复成 hybrid", "port link-type hybrid" in body)
    check("access 端口出现两次",
          body.count("port link-type access") == 2,
          f"实际 {body.count('port link-type access')}")

    print("\n[4] 基线里已有该 VLAN：不能整块删")
    base_text = """#
 vlan 10
#
 interface GigabitEthernet1/0/1
 port link-type access
#
"""
    out, _ = run("h3c", "vlan_port", params, base_text)
    body = [ln.strip() for ln in lines_of(out)]
    check("不删已存在的 VLAN 10", "undo vlan 10" not in body)
    check("新增的 VLAN 20 仍然删掉", "undo vlan 20" in body)

    print("\n[5] 端口是本次新增的：仍然走 undo")
    base_text = """#
 vlan 10
#
"""
    out, _ = run("h3c", "vlan_port", params, base_text)
    body = [ln.strip() for ln in lines_of(out)]
    check("查不到原值时回落到 undo",
          "undo port link-type" in body)

    print("\n[6] 思科侧同样生效")
    cisco_params = {
        "vlans": "10,20", "vlan_names": "", "port_type": "trunk",
        "ports": "1/0/1-2", "allowed": "10,20", "pvid": "1",
    }
    base_text = """!
interface GigabitEthernet1/0/1
 switchport mode access
!
interface GigabitEthernet1/0/2
 switchport mode access
!
"""
    out, base = run("cisco", "vlan_port", cisco_params, base_text)
    body = [ln.strip() for ln in lines_of(out)]
    check("恢复成 switchport mode access",
          "switchport mode access" in body)
    check("不再出现 no switchport mode",
          "no switchport mode" not in body)

    print()
    if fails:
        print(f"失败 {len(fails)} 项：{'、'.join(fails)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
