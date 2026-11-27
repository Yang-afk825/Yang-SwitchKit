"""验收清单：这批命令下发之后，敲什么确认它真的生效了。

为什么单独做一层
---------------
工具原来只负责「写」，写完就结束了。但变更现场真正的风险点在下发之后：
命令进了设备、`display` 一看没生效，或者生效了但不是你以为的那个效果。
配错了能立刻看出来，**配了没生效反而容易被当成成功**——半个月后业务不通
才发现，那时候已经没人记得当时动了什么。

所以每个场景配一组检查：「敲哪条、看什么、看到什么算对」。
这就是老网工脑子里那套东西，写下来给新人用。

为什么按场景而不是按命令
-----------------------
``explain`` 里有 170 条概念，逐条给验证命令能覆盖得更细，但工作量是
29 个场景的六倍，而收益不成比例——同一批命令下发后，网工要确认的是
「VLAN 有没有、口对不对、通不通」，不是「每一条命令各自的副作用」。
按场景给 2-4 条，覆盖变更验收的实际颗粒度。

为什么命令不带具体参数
---------------------
``display port vlan GigabitEthernet1/0/24`` 比 ``display port vlan`` 精确，
但端口名一旦和实际不一致就白敲。这里给不带参数的通用查询，产出一屏
完整状态，再用一句「重点核对哪几个口」告诉人该看哪里。
宁可多看一眼整屏，也不要敲一条不存在的接口名。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ports as portmod


@dataclass(frozen=True)
class Check:
    """一条验收检查。"""

    title: str
    command: str
    expect: str = ""

    def render(self, index: int = 0) -> list:
        head = f"{index}. {self.title}" if index else self.title
        out = [head, f"   {self.command}"]
        if self.expect:
            out.append(f"   → {self.expect}")
        return out


#: 概念 → 四家命令写法。缺哪家就退回思科那条，反正都是 show/display。
_CMD = {
    "vlan": {"h3c": "display vlan", "huawei": "display vlan",
             "cisco": "show vlan brief", "ruijie": "show vlan"},
    "port_vlan": {"h3c": "display port vlan", "huawei": "display port vlan",
                  "cisco": "show interfaces switchport",
                  "ruijie": "show interfaces switchport"},
    "iface_brief": {"h3c": "display interface brief",
                    "huawei": "display interface brief",
                    "cisco": "show ip interface brief",
                    "ruijie": "show ip interface brief"},
    "iface_detail": {"h3c": "display interface", "huawei": "display interface",
                     "cisco": "show interfaces", "ruijie": "show interfaces"},
    "link_agg": {"h3c": "display link-aggregation summary",
                 "huawei": "display eth-trunk",
                 "cisco": "show etherchannel summary",
                 "ruijie": "show aggregateport summary"},
    "stp": {"h3c": "display stp brief", "huawei": "display stp brief",
            "cisco": "show spanning-tree", "ruijie": "show spanning-tree"},
    "dhcp_pool": {"h3c": "display dhcp server pool",
                  "huawei": "display ip pool",
                  "cisco": "show ip dhcp pool", "ruijie": "show ip dhcp pool"},
    "dhcp_bind": {"h3c": "display dhcp server ip-in-use",
                  "huawei": "display ip pool used",
                  "cisco": "show ip dhcp binding", "ruijie": "show ip dhcp binding"},
    "route": {"h3c": "display ip routing-table", "huawei": "display ip routing-table",
              "cisco": "show ip route", "ruijie": "show ip route"},
    "acl": {"h3c": "display acl all", "huawei": "display acl all",
            "cisco": "show access-lists", "ruijie": "show access-lists"},
    "ssh": {"h3c": "display ssh server status",
            "huawei": "display ssh server status",
            "cisco": "show ip ssh", "ruijie": "show ip ssh"},
    "ntp": {"h3c": "display ntp-service status",
            "huawei": "display ntp-service status",
            "cisco": "show ntp status", "ruijie": "show ntp status"},
    "port_sec": {"h3c": "display port-security",
                 "huawei": "display port-security",
                 "cisco": "show port-security", "ruijie": "show port-security"},
    "mac_table": {"h3c": "display mac-address", "huawei": "display mac-address",
                  "cisco": "show mac address-table",
                  "ruijie": "show mac-address-table"},
    "arp": {"h3c": "display arp", "huawei": "display arp",
            "cisco": "show arp", "ruijie": "show arp"},
    "vrrp": {"h3c": "display vrrp", "huawei": "display vrrp",
             "cisco": "show standby brief", "ruijie": "show vrrp"},
    "ospf": {"h3c": "display ospf peer", "huawei": "display ospf peer",
             "cisco": "show ip ospf neighbor", "ruijie": "show ip ospf neighbor"},
    "bgp": {"h3c": "display bgp peer", "huawei": "display bgp peer",
            "cisco": "show ip bgp summary", "ruijie": "show ip bgp summary"},
    "log": {"h3c": "display logbuffer", "huawei": "display logbuffer",
            "cisco": "show logging", "ruijie": "show logging"},
    "clock": {"h3c": "display clock", "huawei": "display clock",
              "cisco": "show clock", "ruijie": "show clock"},
    "running": {"h3c": "display current-configuration",
                "huawei": "display current-configuration",
                "cisco": "show running-config", "ruijie": "show running-config"},
    "igmp": {"h3c": "display igmp-snooping",
             "huawei": "display igmp-snooping",
             "cisco": "show ip igmp snooping",
             "ruijie": "show ip igmp snooping"},
    "dot1x": {"h3c": "display dot1x", "huawei": "display dot1x",
              "cisco": "show dot1x all", "ruijie": "show dot1x"},
    "poe": {"h3c": "display poe interface", "huawei": "display poe interface",
            "cisco": "show power inline", "ruijie": "show poe interface"},
    "mirror": {"h3c": "display mirroring-group all",
               "huawei": "display observe-port",
               "cisco": "show monitor", "ruijie": "show monitor"},
}


def _cmd(vendor_key: str, concept: str) -> str:
    table = _CMD.get(concept, {})
    return (table.get(vendor_key)
            or table.get("cisco")
            or _CMD["running"].get(vendor_key, "show running-config"))


def _ports_hint(params: dict, key: str = "ports") -> str:
    """把端口参数压成一句「重点看哪几个」。"""
    raw = str(params.get(key, "") or "").strip()
    if not raw:
        return ""
    try:
        expanded = portmod.expand(raw, "h3c")
    except Exception:  # noqa: BLE001
        return raw
    if not expanded:
        return raw
    if len(expanded) <= 3:
        return "、".join(expanded)
    return f"{expanded[0]}、{expanded[1]} … 共 {len(expanded)} 个"


# ==========================================================================
# 各场景的检查项
# ==========================================================================

def _vlan_port(v: str, p: dict) -> list:
    hint = _ports_hint(p)
    return [
        Check("VLAN 是否建好", _cmd(v, "vlan"), "应能看到目标 VLAN，状态为 active"),
        Check("端口 VLAN 归属", _cmd(v, "port_vlan"),
              f"重点核对 {hint}，PVID 应为预期值" if hint else "PVID 应为预期值"),
        Check("接口是否 up", _cmd(v, "iface_brief"),
              "目标端口状态应为 UP，协议状态 UP"),
    ]


def _lag(v: str, p: dict) -> list:
    return [
        Check("聚合组是否建立", _cmd(v, "link_agg"),
              "成员口状态应为 Selected / 已选中，不能是 Unselected"),
        Check("成员口是否都被收进来", _cmd(v, "iface_brief"),
              "成员口应显示为聚合成员，物理状态 UP"),
    ]


def _stp(v: str, p: dict) -> list:
    return [
        Check("生成树状态", _cmd(v, "stp"), "根桥 / 端口角色符合设计，没有意外阻塞"),
        Check("边缘端口是否生效", _cmd(v, "stp"),
              "接了终端的口应为 Edge / PortFast，且不参与收敛"),
    ]


def _dhcp(v: str, p: dict) -> list:
    return [
        Check("地址池是否建成", _cmd(v, "dhcp_pool"), "池的网段、网关、DNS 与规划一致"),
        Check("有没有客户端拿到地址", _cmd(v, "dhcp_bind"),
              "终端重新获取地址后，这里应出现绑定记录"),
    ]


def _route(v: str, p: dict) -> list:
    return [
        Check("路由是否进表", _cmd(v, "route"), "目标网段出现在路由表，下一跳正确"),
        Check("是不是真的通", "ping <对端地址>",
              "能通，且 traceroute 走的是预期路径"),
    ]


def _acl(v: str, p: dict) -> list:
    return [
        Check("ACL 规则是否生效", _cmd(v, "acl"),
              "规则顺序与设计一致，deny 在 permit 之前的话要确认是故意的"),
        Check("ACL 有没有挂到接口", _cmd(v, "running"),
              "目标接口下应有 traffic-filter / ip access-group"),
    ]


def _mgmt(v: str, p: dict) -> list:
    return [
        Check("SSH 服务是否起来", _cmd(v, "ssh"), "服务状态 enabled，版本符合要求"),
        Check("时间是否同步", _cmd(v, "ntp"), "时钟已同步，层级不是 16（16 表示未同步）"),
        Check("配置是否落盘", _cmd(v, "running"), "刚才的命令都在运行配置里"),
    ]


def _port_isolation(v: str, p: dict) -> list:
    return [
        Check("隔离是否生效", _cmd(v, "port_vlan"),
              "同组端口之间应该 ping 不通，到上行口应该通"),
        Check("上行口是否豁免", _cmd(v, "running"), "上行口不在隔离组里"),
    ]


def _port_security(v: str, p: dict) -> list:
    return [
        Check("端口安全是否启用", _cmd(v, "port_sec"),
              "目标端口的 max-mac 与违规动作与设计一致"),
        Check("MAC 表是否学到", _cmd(v, "mac_table"),
              "接上终端后应看到对应 MAC 绑定在该端口"),
    ]


def _ospf(v: str, p: dict) -> list:
    return [
        Check("邻居是否 Full", _cmd(v, "ospf"), "State 应为 Full，不能停在 ExStart / 2-Way"),
        Check("路由是否学到", _cmd(v, "route"), "对端网段应通过 OSPF 学进来"),
    ]


def _bgp(v: str, p: dict) -> list:
    return [
        Check("邻居是否 Established", _cmd(v, "bgp"),
              "State 应为 Established；Idle / Active 说明 TCP 都没建起来"),
        Check("路由是否收到", _cmd(v, "route"), "对端宣告的网段应出现在 BGP 路由里"),
    ]


def _syslog(v: str, p: dict) -> list:
    return [
        Check("时钟是否对", _cmd(v, "clock"), "时间与当前实际时间一致，时区正确"),
        Check("日志有没有上去", _cmd(v, "log"),
              "本地日志里能看到刚才的操作记录；服务器侧也应收到"),
    ]


def _gateway(v: str, p: dict) -> list:
    return [
        Check("冗余组状态", _cmd(v, "vrrp"), "Master / Backup 角色符合设计"),
        Check("虚拟地址是否生效", _cmd(v, "iface_detail"),
              "终端 ping 虚拟网关应该通"),
    ]


def _mirror(v: str, p: dict) -> list:
    return [
        Check("镜像组是否建立", _cmd(v, "mirror"),
              "源端口、方向、观察口三者与设计一致"),
        Check("观察口能不能收到流量", _cmd(v, "iface_detail"),
              "抓包侧应能看到被镜像的流量"),
    ]


def _igmp(v: str, p: dict) -> list:
    return [
        Check("Snooping 是否启用", _cmd(v, "igmp"), "对应 VLAN 已开启"),
        Check("组播组是否学到", _cmd(v, "igmp"),
              "终端加入组播后，这里应出现组地址和成员端口"),
    ]


def _dot1x(v: str, p: dict) -> list:
    return [
        Check("认证状态", _cmd(v, "dot1x"), "端口应为 Authenticated"),
        Check("RADIUS 是否可达", _cmd(v, "running"), "服务器地址与密钥配置正确"),
    ]


def _poe(v: str, p: dict) -> list:
    return [
        Check("供电状态", _cmd(v, "poe"), "目标端口应显示 Delivering Power"),
        Check("功率是否够", _cmd(v, "poe"), "总功率不超过设备上限"),
    ]


def _generic(v: str, p: dict) -> list:
    return [
        Check("命令是否都进去了", _cmd(v, "running"),
              "刚下发的命令应全部出现在运行配置里"),
        Check("接口状态有没有变化", _cmd(v, "iface_brief"), "不应出现意外 down"),
    ]


_SCENES = {
    "vlan_port": _vlan_port,
    "lag": _lag,
    "stp": _stp,
    "dhcp": _dhcp,
    "static_route": _route,
    "acl": _acl,
    "mgmt": _mgmt,
    "port_isolation": _port_isolation,
    "port_security": _port_security,
    "ospf": _ospf,
    "bgp": _bgp,
    "syslog": _syslog,
    "gateway": _gateway,
    "mirror": _mirror,
    "igmp_snooping": _igmp,
    "dot1x": _dot1x,
    "poe": _poe,
    "loop_guard": _stp,
    "arp_guard": lambda v, p: [
        Check("ARP 表是否正常", _cmd(v, "arp"), "表项与终端实际情况一致，没有冲突"),
        Check("防护有没有挂上", _cmd(v, "running"), "目标 VLAN 已开启检测"),
    ],
    "dhcp_snooping": lambda v, p: [
        Check("绑定表是否建立", _cmd(v, "dhcp_bind"), "接入终端后应有绑定记录"),
        Check("信任口是否配置", _cmd(v, "running"), "只有上行口是信任口"),
    ],
    "storm_control": lambda v, p: [
        Check("抑制阈值", _cmd(v, "iface_detail"), "阈值与设计一致"),
        Check("有没有误伤", _cmd(v, "iface_brief"), "正常业务下不应出现持续丢包"),
    ],
    "qos_rate": lambda v, p: [
        Check("限速是否生效", _cmd(v, "iface_detail"), "接口下有速率限制配置"),
        Check("实测带宽", "在终端侧打流验证", "实测速率不超过设定值"),
    ],
    "qos_priority": lambda v, p: [
        Check("信任模式", _cmd(v, "iface_detail"), "trust 配置符合设计"),
        Check("重标记是否生效", _cmd(v, "acl"), "ACL 规则与 DSCP 映射正确"),
    ],
    "stacking": lambda v, p: [
        Check("成员是否都起来", _cmd(v, "iface_brief"), "所有成员设备都在，没有分裂"),
        Check("主备角色", _cmd(v, "running"), "master / standby 符合设计"),
    ],
    "isis": lambda v, p: [
        Check("邻居是否建立", _cmd(v, "route"), "IS-IS 邻居 UP"),
        Check("路由是否学到", _cmd(v, "route"), "目标网段已进路由表"),
    ],
    "mplsvpn": lambda v, p: [
        Check("MPLS 转发是否就绪", _cmd(v, "route"), "标签转发路径完整"),
        Check("VPN 路由是否通", _cmd(v, "route"), "CE 侧网段能互访"),
    ],
    "ipv6": lambda v, p: [
        Check("地址是否配上", _cmd(v, "iface_detail"), "接口有 IPv6 地址且非 tentative"),
        Check("能不能通", "ping ipv6 <对端地址>", "能通"),
    ],
    "backup": lambda v, p: [
        Check("配置是否已保存", _cmd(v, "running"), "当前配置与备份一致"),
    ],
    "inspect": lambda v, p: [],
}


def checks(vendor_key: str, items: list) -> list:
    """按这批场景生成验收清单。

    ``items`` 是 ``[(scenario_key, params), ...]``，顺序即生成顺序。
    同一场景重复出现只算一次（生成侧本来就按场景去重）。
    """
    out: list = []
    seen: set = set()

    for scenario_key, params in items:
        if scenario_key in seen:
            continue
        seen.add(scenario_key)

        handler = _SCENES.get(scenario_key, _generic)
        try:
            got = handler(vendor_key, params or {})
        except Exception:  # noqa: BLE001 —— 某项算不出来不该让整份清单空掉
            got = []
        out.extend(got)

    # 全空时至少给一条兜底，不然报告里这一段是空白
    if not out:
        out = _generic(vendor_key, {})
    return out


def render(vendor_key: str, items: list) -> list:
    """渲染成文本行，给变更包和界面用。"""
    lines = ["━━ 验收清单 ━━",
             "",
             "命令下发后逐条执行，确认结果与预期一致。",
             "任何一条不符，先停下排查，不要继续往下走。",
             ""]

    got = checks(vendor_key, items)
    for index, item in enumerate(got, 1):
        lines.extend(item.render(index))
        lines.append("")
    return lines
