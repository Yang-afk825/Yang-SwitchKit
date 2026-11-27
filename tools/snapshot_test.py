"""现网解析自检：四家配置各来一份，验证字段读得对。

跑法：

    py -3.12 tools/snapshot_test.py

样本是照着四家真实 ``display current-configuration`` 输出缩写的，
保留了容易踩的形态：H3C 的 ``permit vlan 10 20 30`` 空格式列表、
华为的 ``allow-pass``、思科的 ``allowed vlan 10,20,30`` 逗号式、
以及 ``interface Vlan-interface 1`` 这种名字里带空格的接口。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scg import snapshot                      # noqa: E402

PASS: list = []
FAIL: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
    else:
        FAIL.append(f"{name}" + (f" —— {detail}" if detail else ""))


H3C = """\
#
 sysname Core-SW-01
#
 vlan 10
  name Office
#
 vlan 20
  name Server
#
 vlan 30
#
interface Vlan-interface 1
 ip address 192.168.1.254 255.255.255.0
#
interface GigabitEthernet1/0/24
 port link-type trunk
 port trunk permit vlan 10 20 30
 port trunk pvid vlan 1
 description Uplink-to-Core
#
interface GigabitEthernet1/0/1
 port link-type access
 port access vlan 10
#
interface GigabitEthernet1/0/2
 port link-type access
 port access vlan 10
#
 ip route-static 0.0.0.0 0 192.168.1.1
#
return
"""

HUAWEI = """\
#
sysname Access-SW-02
#
vlan batch 10 20 30 40
#
interface Vlanif 1
 ip address 10.1.1.2 255.255.255.0
#
interface GigabitEthernet0/0/24
 port link-type trunk
 port trunk allow-pass vlan 10 20 30 40
 port trunk pvid vlan 1
#
interface GigabitEthernet0/0/1
 port link-type access
 port default vlan 20
#
ip route-static 0.0.0.0 0.0.0.0 10.1.1.1
"""

CISCO = """\
hostname Dist-SW
!
vlan 10
vlan 20
vlan 30
!
interface Vlan1
 ip address 172.16.1.1 255.255.255.0
!
interface GigabitEthernet0/24
 switchport mode trunk
 switchport trunk allowed vlan 10,20,30
 switchport trunk native vlan 99
!
interface GigabitEthernet0/1
 switchport mode access
 switchport access vlan 10
!
ip route 0.0.0.0 0.0.0.0 172.16.1.254
!
end
"""

RUIJIE = """\
!
hostname RJ-SW
!
enable service ssh-server
!
vlan 10
vlan 20
!
interface GigabitEthernet 0/1
 switchport mode trunk
 switchport trunk allowed vlan 10,20
!
interface GigabitEthernet 0/2
 switchport mode access
 switchport access vlan 10
!
end
"""

#: 只配了 VLAN 和 trunk 的锐捷配置，跟思科几乎逐字相同，谁都分不出来。
#: 厂商识别靠的是「有没有锐捷独有命令」，不是语法本身——这份样本用来
#: 确认这种情况会退到 ios 族，而不是瞎猜成 H3C
IOS_AMBIGUOUS = """\
hostname SW
vlan 10
vlan 20
interface GigabitEthernet 0/1
 switchport mode trunk
 switchport trunk allowed vlan 10,20
end
"""


def test_vendor() -> None:
    for name, text, expect in (("H3C", H3C, "h3c"), ("华为", HUAWEI, "huawei"),
                               ("思科", CISCO, "cisco"), ("锐捷", RUIJIE, "ruijie")):
        snap = snapshot.parse(text)
        check(f"{name} 厂商识别", snap.vendor == expect,
              f"识别成 {snap.vendor}")

    # 分不出来的时候，至少不能猜成 H3C —— ios 族语法长得完全不一样
    snap = snapshot.parse(IOS_AMBIGUOUS)
    check("思科/锐捷不易区分时归 ios 族",
          snap.vendor in ("cisco", "ruijie"), f"识别成 {snap.vendor}")


def test_vlans() -> None:
    snap = snapshot.parse(H3C)
    check("H3C VLAN 列表", snap.vlans == [10, 20, 30], str(snap.vlans))

    snap = snapshot.parse(HUAWEI)
    check("华为 vlan batch", snap.vlans == [10, 20, 30, 40], str(snap.vlans))

    snap = snapshot.parse(CISCO)
    check("思科 VLAN 列表", snap.vlans == [10, 20, 30], str(snap.vlans))

    check("VLAN 文本", snapshot.parse(H3C).vlan_text() == "10,20,30",
          snapshot.parse(H3C).vlan_text())


def test_ifaces() -> None:
    snap = snapshot.parse(H3C)
    uplink = snap.ifaces.get("GigabitEthernet1/0/24")
    check("H3C trunk 口识别", uplink is not None and uplink.mode == "trunk",
          str(uplink))
    if uplink:
        check("H3C 放通列表", uplink.allowed_list() == [10, 20, 30],
              str(uplink.allowed_list()))
        check("H3C PVID", uplink.pvid == "1", uplink.pvid)
        check("H3C 描述", uplink.desc == "Uplink-to-Core", uplink.desc)

    acc = snap.ifaces.get("GigabitEthernet1/0/1")
    check("H3C access 口", acc is not None and acc.access_vlan == "10", str(acc))

    snap = snapshot.parse(HUAWEI)
    up = snap.ifaces.get("GigabitEthernet0/0/24")
    check("华为 allow-pass 放通", up is not None and up.allowed_list() == [10, 20, 30, 40],
          str(up.allowed_list()) if up else "没识别到")

    snap = snapshot.parse(CISCO)
    up = snap.ifaces.get("GigabitEthernet0/24")
    check("思科 allowed 逗号列表", up is not None and up.allowed_list() == [10, 20, 30],
          str(up.allowed_list()) if up else "没识别到")
    check("思科 native vlan 当 PVID", up is not None and up.pvid == "99",
          up.pvid if up else "")


def test_mgmt() -> None:
    snap = snapshot.parse(H3C)
    check("H3C 管理地址", snap.mgmt_ip == "192.168.1.254", snap.mgmt_ip)
    check("H3C 管理 VLAN", snap.mgmt_vlan == "1", snap.mgmt_vlan)
    check("H3C 网关", snap.gateway == "192.168.1.1", snap.gateway)

    snap = snapshot.parse(HUAWEI)
    check("华为管理地址", snap.mgmt_ip == "10.1.1.2", snap.mgmt_ip)
    check("华为管理 VLAN", snap.mgmt_vlan == "1", snap.mgmt_vlan)

    snap = snapshot.parse(CISCO)
    check("思科管理地址", snap.mgmt_ip == "172.16.1.1", snap.mgmt_ip)


def test_routes() -> None:
    snap = snapshot.parse(H3C)
    check("H3C 默认路由", snap.routes and snap.routes[0] == "0.0.0.0/0 → 192.168.1.1",
          str(snap.routes))

    snap = snapshot.parse(CISCO)
    check("思科默认路由", snap.routes and snap.routes[0].startswith("0.0.0.0/0"),
          str(snap.routes))


def test_uplink() -> None:
    snap = snapshot.parse(H3C)
    up = snap.uplink_iface()
    check("上联口猜对", up is not None and up.name == "GigabitEthernet1/0/24",
          up.name if up else "没猜到")

    # permit vlan all 的口一定是上联
    text = ("#\ninterface GigabitEthernet1/0/1\n port link-type trunk\n"
            " port trunk permit vlan all\n#\n"
            "interface GigabitEthernet1/0/2\n port link-type trunk\n"
            " port trunk permit vlan 10\n#\n")
    up = snapshot.parse(text).uplink_iface()
    check("all 优先级最高", up is not None and up.name == "GigabitEthernet1/0/1",
          up.name if up else "没猜到")


def test_suggest() -> None:
    snap = snapshot.parse(H3C)

    got = snapshot.suggest("h3c", "vlan_port", snap)
    check("建议 VLAN 列表", got.get("vlans") == "10,20,30", str(got))
    check("建议上联放通", got.get("allowed") == "10,20,30", str(got))
    check("建议 PVID", got.get("pvid") == "1", str(got))

    got = snapshot.suggest("h3c", "mgmt", snap)
    check("建议管理地址", got.get("mgmt_ip") == "192.168.1.254", str(got))
    check("建议主机名", got.get("sysname") == "Core-SW-01", str(got))
    check("建议网关", got.get("gateway") == "192.168.1.1", str(got))

    got = snapshot.suggest("h3c", "static_route", snap)
    check("建议路由", "0.0.0.0/0" in str(got.get("routes", "")), str(got))

    # 没读到的场景不该硬给值
    check("无建议时返回空", snapshot.suggest("h3c", "bgp", snap) == {})


def test_vlan_list() -> None:
    cases = (
        ("10 20 30", [10, 20, 30]),
        ("10,20,30", [10, 20, 30]),
        ("10 to 12", [10, 11, 12]),
        ("10-12", [10, 11, 12]),
        ("", []),
        ("all", []),
        ("4095", []),        # 超范围丢掉
        ("12 to 10", [10, 11, 12]),   # 反着写也认
    )
    for text, expect in cases:
        got = snapshot.parse_vlan_list(text)
        check(f"VLAN 列表解析 {text!r}", got == expect, f"得到 {got}")


def test_empty() -> None:
    snap = snapshot.parse("")
    check("空输入不崩", snap.vendor == "" and snap.vlans == [])

    snap = snapshot.parse("这不是配置")
    check("垃圾输入不崩", snap.vlans == [] and not snap.complete)

    check("空摘要能生成", isinstance(snapshot.summary_lines(snap), list))


def test_summary() -> None:
    lines = snapshot.summary_lines(snapshot.parse(H3C))
    body = "\n".join(lines)
    check("摘要含厂商", "H3C" in body, body[:80])
    check("摘要含 VLAN", "10,20,30" in body, body[:120])
    check("摘要含上联口", "GigabitEthernet1/0/24" in body, body[:200])
    check("摘要含管理地址", "192.168.1.254" in body, body[:250])


def main() -> int:
    test_vendor()
    test_vlans()
    test_ifaces()
    test_mgmt()
    test_routes()
    test_uplink()
    test_suggest()
    test_vlan_list()
    test_empty()
    test_summary()

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
