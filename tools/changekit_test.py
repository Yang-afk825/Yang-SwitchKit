"""变更包自检：五段齐全、内容非空、空输入不崩。

跑法：

    py -3.12 tools/changekit_test.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scg import changekit, registry, snapshot       # noqa: E402
from scg.vendors import generate                    # noqa: E402

PASS: list = []
FAIL: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
    else:
        FAIL.append(f"{name}" + (f" —— {detail}" if detail else ""))


VLAN_PARAMS = {
    "vlans": "40", "vlan_names": "", "port_type": "access",
    "ports": "1/0/1-4", "allowed": "", "pvid": "",
}

SNAP_TEXT = """\
vlan 10
vlan 20
#
interface GigabitEthernet1/0/24
 port link-type trunk
 port trunk permit vlan 10 20
 port trunk pvid vlan 1
#
interface Vlan-interface 1
 ip address 192.168.1.254 255.255.255.0
#
return
"""


def _make(vendor: str, key: str, params: dict):
    scenario = registry.get(key)
    return generate(vendor, key, params, scenario)


def test_sections() -> None:
    result = _make("h3c", "vlan_port", VLAN_PARAMS)
    items = [("vlan_port", VLAN_PARAMS)]
    body = "\n".join(changekit.build("h3c", "H3C", items, {"vlan_port": result}))

    for mark in ("一、变更前状态", "二、配置命令", "三、回滚命令",
                 "四、验收清单", "五、影响范围"):
        check(f"含「{mark}」", mark in body, "缺这一段")

    check("含厂商名", "厂商：H3C" in body)
    check("含版本号", "工具版本" in body)
    check("含免责说明", "不连接设备" in body, "结尾说明缺了")


def test_commands() -> None:
    result = _make("h3c", "vlan_port", VLAN_PARAMS)
    items = [("vlan_port", VLAN_PARAMS)]
    body = "\n".join(changekit.build("h3c", "H3C", items, {"vlan_port": result}))

    check("命令段含 vlan 40", "vlan 40" in body, "生成的命令没进变更包")
    check("命令段含接口", "GigabitEthernet" in body)


def test_rollback() -> None:
    result = _make("h3c", "vlan_port", VLAN_PARAMS)
    items = [("vlan_port", VLAN_PARAMS)]
    body = "\n".join(changekit.build("h3c", "H3C", items, {"vlan_port": result}))

    check("回滚段含 undo", "undo" in body.lower() or "no " in body,
          "回滚命令没生成")
    check("回滚段有说明", "默认值" in body, "缺「撤销回到默认值」的提醒")


def test_accept() -> None:
    result = _make("h3c", "vlan_port", VLAN_PARAMS)
    items = [("vlan_port", VLAN_PARAMS)]
    body = "\n".join(changekit.build("h3c", "H3C", items, {"vlan_port": result}))

    check("验收段含 display", "display" in body, "没有验收命令")
    check("验收段含预期", "→" in body, "缺「看到什么算对」")


def test_impact() -> None:
    result = _make("h3c", "vlan_port", VLAN_PARAMS)
    items = [("vlan_port", VLAN_PARAMS)]
    body = "\n".join(changekit.build("h3c", "H3C", items, {"vlan_port": result}))

    check("影响范围含 VLAN", "VLAN：40" in body, "没提到 VLAN")
    check("影响范围含端口", "端口：1/0/1-4" in body, "没提到端口")


def test_snapshot_section() -> None:
    result = _make("h3c", "vlan_port", VLAN_PARAMS)
    items = [("vlan_port", VLAN_PARAMS)]
    snap = snapshot.parse(SNAP_TEXT)

    body = "\n".join(changekit.build("h3c", "H3C", items,
                                     {"vlan_port": result}, snap=snap))
    check("有现状时写出现状", "Trunk 口" in body and "192.168.1.254" in body,
          "现网摘要没进变更包")

    body = "\n".join(changekit.build("h3c", "H3C", items, {"vlan_port": result}))
    check("没现状时给出提示", "没有导入现网配置" in body, "缺提示")


def test_multi_scene() -> None:
    p1 = dict(VLAN_PARAMS)
    p2 = {"routes": "0.0.0.0/0 → 192.168.1.1", "pref": "", "desc": ""}
    results = {
        "vlan_port": _make("h3c", "vlan_port", p1),
        "static_route": _make("h3c", "static_route", p2),
    }
    items = [("vlan_port", p1), ("static_route", p2)]
    lines = changekit.build("h3c", "H3C", items, results)
    body = "\n".join(lines)

    check("多场景两个标题", body.count("【") >= 2, "场景标题不够")
    check("多场景场景数", "场景：2 个" in body, "场景数不对")


def test_empty() -> None:
    lines = changekit.build("h3c", "H3C", [], {})
    check("空输入不崩", isinstance(lines, list) and len(lines) > 10)
    check("空输入有说明", "没有可回滚的内容" in "\n".join(lines)
          or "没有可自动撤销" in "\n".join(lines))


def test_filename() -> None:
    items = [("vlan_port", VLAN_PARAMS)]
    name = changekit.filename("H3C", items)
    check("文件名带扩展名", name.endswith(".txt"), name)
    check("文件名带厂商", "H3C" in name, name)
    check("文件名无非法字符", not any(c in name for c in '\\/:*?"<>|'), name)


def main() -> int:
    test_sections()
    test_commands()
    test_rollback()
    test_accept()
    test_impact()
    test_snapshot_section()
    test_multi_scene()
    test_empty()
    test_filename()

    print(f"通过 {len(PASS)} 项")
    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：")
        for item in FAIL:
            print(f"  ✗ {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
