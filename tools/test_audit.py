"""体检规则自测：用几段刻意写坏的配置，确认该报的都报出来了。

用法：python tools/test_audit.py [--render]
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import audit                      # noqa: E402

# 刻意写坏的 H3C 配置：Telnet 开着、SNMP 用 public、口令明文、VLAN 1 有三层地址
BAD_H3C = """
#
 sysname OLD-SW-01
#
 telnet server enable
#
 vlan 1
#
 interface Vlan-interface 1
 ip address 192.168.1.2 255.255.255.0
#
 local-user admin class manage
 password simple 123456
 service-type telnet
#
 snmp-agent
 snmp-agent community read simple public
 snmp-agent sys-info version v2c
#
 interface GigabitEthernet1/0/1
 port link-type access
 port access vlan 10
#
 interface GigabitEthernet1/0/2
 port link-type access
 port access vlan 10
#
 interface GigabitEthernet1/0/3
 port link-type access
 port access vlan 10
#
 interface GigabitEthernet1/0/4
 port link-type access
 port access vlan 10
#
 interface GigabitEthernet1/0/5
 port link-type trunk
 port trunk permit vlan 1 10 20
#
 ip route-static 0.0.0.0 0 192.168.1.1
#
"""

# 刻意写坏的思科配置：Telnet 允许、HTTP 开着、无 NTP
BAD_CISCO = """
!
hostname OLD-SW-02
!
ip http server
ip http secure-server
!
interface Vlan1
 ip address 10.1.1.2 255.255.255.0
!
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 10
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport access vlan 10
!
line vty 0 4
 transport input telnet
!
snmp-server community private RW
!
end
"""

# 相对规范的配置：应该拿到高分
GOOD_H3C = """
#
 sysname CORE-01
#
 clock timezone UTC add 08:00:00
#
 vlan 99
#
 interface Vlan-interface 99
 ip address 10.0.99.1 255.255.255.0
#
 local-user netadmin class manage
 password cipher $c$3$abcdef
 service-type ssh
 authorization-attribute user-role network-admin
#
 line vty 0 4
 authentication-mode scheme
 protocol inbound ssh
#
 ssh server enable
 undo telnet server enable
#
 ntp-service enable
 ntp-service unicast-server 10.0.0.5
#
 info-center enable
 info-center loghost 10.0.0.7
#
 stp global enable
 stp mode rstp
 stp root primary
 stp bpdu-protection
#
 interface GigabitEthernet1/0/1
 port link-type access
 port access vlan 10
 stp edged-port
#
 interface GigabitEthernet1/0/2
 port link-type access
 port access vlan 10
 stp edged-port
#
 interface GigabitEthernet1/0/3
 shutdown
#
 snmp-agent
 snmp-agent community read cipher S3cure@Read
 snmp-agent sys-info version v3
#
"""

CASES = (
    ("H3C 反面样本", BAD_H3C, "h3c"),
    ("思科 反面样本", BAD_CISCO, "cisco"),
    ("H3C 规范样本", GOOD_H3C, "h3c"),
)

EXPECT_BAD = (
    "设备开着 Telnet 服务",
    "SNMP 团体字用了默认值或弱口令",
    "存在明文保存的口令",
    "VLAN 1 上有三层地址",
)


def main() -> int:
    render = "--render" in sys.argv
    fails: list = []

    for name, text, want_vendor in CASES:
        report = audit.analyze(text)
        print(f"\n=== {name} ===")
        print(f"识别 {report.vendor_name}（{report.vendor_evidence}）  分数 {report.score}")
        print(f"高 {len(report.high)} / 中 {len(report.mid)} / 低 {len(report.low)} / "
              f"通过 {len(report.passed)}")

        if report.vendor_key != want_vendor:
            fails.append((name, f"厂商识别错：{report.vendor_key} != {want_vendor}"))

        for title in report.high + report.mid + report.low:
            print(f"   [{title.level}] {title.title}")

        if name.endswith("反面样本"):
            titles = {item.title for item in report.findings}
            for want in EXPECT_BAD:
                if want == "VLAN 1 上有三层地址" and name.startswith("思科"):
                    continue          # 思科这条在 fixture 里是 interface Vlan1
                if want == "存在明文保存的口令" and name.startswith("思科"):
                    continue
                if want not in titles:
                    fails.append((name, f"漏报：{want}"))

        if name.startswith("H3C 规范") and report.score < 80:
            fails.append((name, f"规范配置分数偏低：{report.score}"))

        if render and name.startswith("H3C 反面"):
            print("\n".join(audit.render(report)))

    # 厂商识别的边界：空文本、无关文本
    if audit.analyze("").findings:
        fails.append(("空输入", "空文本不应产出结论"))
    junk = audit.analyze("这是一段完全无关的文字，没有任何设备配置。")
    if junk.vendor_key:
        fails.append(("无关文本", f"不该识别出厂商：{junk.vendor_key}"))

    print()
    if fails:
        print("问题：")
        for name, msg in fails:
            print(f"  [{name}] {msg}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
