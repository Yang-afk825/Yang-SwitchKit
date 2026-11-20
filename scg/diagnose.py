"""故障排查决策树。

和前面几个模块的定位不同：这里不生成配置，也不改配置，只回答一个问题 ——
**「现象是这样，接下来该查什么」**。

真实排障最耗时的不是敲命令，是决定下一条命令敲什么。老网工脑子里有一棵
决策树，新人没有。这个模块就是把那棵树写下来：

    现象 -> 步骤 1（查什么 + 命令 + 怎么判） -> 步骤 2 -> ...

每一步都写清楚「看到什么算正常、看到什么该往哪走」，不给一句
「检查配置是否正确」这种等于没说的话。
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """一个排查步骤。"""

    check: str                    # 这一步要确认什么
    commands: dict               # {"h3c": [...]} 通用；{"h3c": [...]} 厂商专属
    judge: str = ""              # 结果怎么判读
    then: str = ""               # 判读之后往哪走

    def commands_for(self, vendor_key: str) -> list:
        """按厂商族取命令。

        Comware 系（H3C / 华为）和 IOS 系（思科 / 锐捷）的命令差别是根本性的，
        所以用 ``h3c`` / ``cisco`` 两个族名存，再叠加个别厂商的专属写法
        （比如华为的 ``display eth-trunk``、锐捷的 ``show aggregateport``）。
        """
        family = "h3c" if vendor_key in ("h3c", "huawei") else "cisco"
        out = list(self.commands.get(family, []))
        if vendor_key != family:
            out += list(self.commands.get(vendor_key, []))

        seen: set = set()
        uniq: list = []
        for command in out:
            if command not in seen:
                seen.add(command)
                uniq.append(command)
        return uniq


@dataclass(frozen=True)
class Case:
    key: str
    name: str
    symptom: str
    steps: tuple
    pitfalls: tuple = ()


_VENDOR_LABEL = {
    "h3c": "H3C / 华为（Comware / VRP）",
    "huawei": "H3C / 华为（Comware / VRP）",
    "cisco": "思科 / 锐捷（IOS / RGOS）",
    "ruijie": "思科 / 锐捷（IOS / RGOS）",
}


# --------------------------------------------------------------------------
# 案例
# --------------------------------------------------------------------------

CASES: tuple = (
    Case(
        key="vlan_route",
        name="VLAN 间不通",
        symptom="同一台交换机下两个不同 VLAN 的终端互相 ping 不通，"
                "但各自 ping 自己的网关是通的。",
        steps=(
            Step(
                "确认两个终端所在的端口真的在对应 VLAN 里",
                {"h3c": ["display vlan brief", "display interface brief"],
                 "cisco": ["show vlan brief", "show interfaces status"],
                 "ruijie": ["show vlan", "show interfaces status"]},
                "端口状态要是 UP，且在 VLAN 成员列表里能看到它。"
                "端口是 DOWN 就先解决物理层，别往下查。",
                "端口不在正确的 VLAN -> 查 port access vlan / switchport access vlan。"),
            Step(
                "确认两个 VLAN 的三层接口都配了地址并且是 UP",
                {"h3c": ["display ip interface brief"],
                 "cisco": ["show ip interface brief"],
                 "ruijie": ["show ip interface brief"]},
                "Vlan-interface / Vlanif / Vlan 的状态必须是 UP，"
                "而且 Physical 那一列也得是 UP —— SVI 只在 VLAN 内有 UP 端口时才起来。",
                "接口 DOWN 但 VLAN 内有 UP 端口 -> 查 VLAN 是否被 shutdown。"
                "接口没地址 -> 补 ip address。"),
            Step(
                "确认设备本身有到对端网段的路由",
                {"h3c": ["display ip routing-table"],
                 "cisco": ["show ip route"],
                 "ruijie": ["show ip route"]},
                "直连网段应该以 Direct 出现。没有直连路由说明三层接口没起来。",
                "有直连但没有对端更远的网段 -> 问题在路由，不在二层。"),
            Step(
                "确认没有 ACL 把流量拦掉",
                {"h3c": ["display acl all", "display packet-filter statistics"],
                 "cisco": ["show access-lists", "show ip interface"],
                 "ruijie": ["show access-lists"]},
                "看 Vlan-interface 上有没有 inbound / outbound 的 ACL，"
                "以及 ACL 的命中计数有没有在涨。",
                "计数在涨说明流量被 ACL 拦了，逐条看 deny 规则。"),
            Step(
                "确认终端自己的网关和掩码指对了",
                {"h3c": []},
                "这一步在终端上做：ipconfig / ifconfig 看网关是不是三层接口地址，"
                "掩码是不是和网段匹配。",
                "网关指错是最常见的原因，而且看起来和交换机一点关系都没有。"),
        ),
        pitfalls=(
            "终端网关填成了别的 VLAN 的地址 —— 现象和「VLAN 间不通」一模一样。",
            "Windows 防火墙默认挡 ICMP，ping 不通不代表网络不通，先关防火墙再测。",
            "SVI 起来了但 VLAN 里没有 UP 的端口时，SVI 会自己 down 掉。",
        ),
    ),
    Case(
        key="lag_down",
        name="链路聚合起不来",
        symptom="配了聚合口，但成员口没有全部加入，或者聚合口状态一直是 DOWN。",
        steps=(
            Step(
                "看聚合组和成员口的真实状态",
                {"h3c": ["display link-aggregation verbose"],
                 "huawei": ["display eth-trunk 1"],
                 "cisco": ["show etherchannel summary"],
                 "ruijie": ["show aggregateport summary"]},
                "成员口要处于 Selected（思科是 P/In use）才算真正生效。"
                "Unselected / 独立 说明协商没通过。",
                "全部 Unselected -> 往下一步查一致性。"),
            Step(
                "确认成员口的速率、双工、VLAN 配置完全一致",
                {"h3c": ["display interface brief"],
                 "cisco": ["show interfaces status"],
                 "ruijie": ["show interfaces status"]},
                "速率和双工必须一样。一端协商成千兆全双工、另一端百兆半双工，"
                "聚合就起不来，而且日志里不一定报错。",
                "不一致 -> 手动把两端 speed / duplex 固定成相同值。"),
            Step(
                "确认成员口上没有被单独配过业务命令",
                {"h3c": ["display current-configuration interface <成员口>"],
                 "cisco": ["show running-config interface <成员口>"],
                 "ruijie": ["show running-config interface <成员口>"]},
                "成员口加入聚合组之后，端口上的 VLAN 配置应该由聚合口统一下发。"
                "如果成员口上还挂着 port access vlan 之类的命令，会冲突。",
                "有残留命令 -> 清掉，让配置只留在聚合口上。"),
            Step(
                "跨设备聚合要确认堆叠 / M-LAG 真的起来了",
                {"h3c": ["display irf", "display stack"],
                 "cisco": ["show switch"],
                 "ruijie": ["show switch", "show vsl"]},
                "普通单机聚合不能跨设备。跨框聚合必须先有 IRF / VSU / StackWise。",
                "堆叠没起来 -> 先解决堆叠，聚合是第二步。"),
        ),
        pitfalls=(
            "LACP 两端模式不一致：一端 active、另一端 passive 能起，"
            "但两端都 passive 就起不来。",
            "光模块单通也会导致成员口 Unselected，看接口计数有没有 err。",
        ),
    ),
    Case(
        key="stp_flap",
        name="网络间歇性卡顿 / STP 震荡",
        symptom="全网时不时卡几秒，日志里能看到 STP 拓扑变化，"
                "或者某几个端口反复 up/down。",
        steps=(
            Step(
                "看拓扑变化计数在不在涨",
                {"h3c": ["display stp brief"],
                 "cisco": ["show spanning-tree summary"],
                 "ruijie": ["show spanning-tree summary"]},
                "重点看 Topology Change 计数。隔几十秒再看一次，"
                "数字持续变大就是真的在震荡。",
                "计数不涨 -> 卡顿来自别处，可能是广播风暴或链路拥塞。"),
            Step(
                "确认根桥在核心，不在接入层",
                {"h3c": ["display stp root"],
                 "cisco": ["show spanning-tree root"],
                 "ruijie": ["show spanning-tree root"]},
                "根桥应该是核心交换机。如果根桥显示的是某台接在机房的接-入交换机，"
                "说明它抢了根，全网流量都要绕过去。",
                "根桥位置不对 -> 核心上 stp root primary，备用核心 stp root secondary。"),
            Step(
                "检查接终端的端口有没有配边缘端口",
                {"h3c": ["display stp brief"],
                 "cisco": ["show spanning-tree interface"],
                 "ruijie": ["show spanning-tree interface"]},
                "接 PC、打印机的端口应该是 EDGE / Edge / Portfast。"
                "终端每次开机、拔插都会触发一次拓扑重算，全网卡两秒。",
                "没配 -> stp edged-port / spanning-tree portfast，"
                "同时必须配 BPDU 保护。"),
            Step(
                "查有没有人私接交换机造成环路",
                {"h3c": ["display mac-address mac-move", "display logbuffer"],
                 "cisco": ["show mac address-table", "show logging"],
                 "ruijie": ["show mac-address-table", "show logging"]},
                "同一个 MAC 在两个端口之间反复漂移，基本就是环路。"
                "日志里会有 MAC 漂移或 BPDU 保护触发的记录。",
                "找到环路端口后先 shutdown，再让用户撤掉私接设备。"),
        ),
        pitfalls=(
            "只配了边缘端口没配 BPDU 保护，等于把抢根桥的机会留给了下游。",
            "生成树模式不一致（一端 MSTP、一端 RSTP）也能跑，但收敛行为会怪。",
        ),
    ),
    Case(
        key="dhcp_fail",
        name="终端拿不到 IP 地址",
        symptom="终端显示 169.254.x.x 或者一直「正在获取 IP 地址」。",
        steps=(
            Step(
                "确认 DHCP 服务本身是开的",
                {"h3c": ["display dhcp server ip-in-use", "display current-configuration | include dhcp"],
                 "cisco": ["show ip dhcp binding", "show running-config | include dhcp"],
                 "ruijie": ["show ip dhcp binding"]},
                "命令能正常返回说明服务在跑。报「DHCP 未使能」就是没开。",
                "没开 -> dhcp enable / service dhcp。"),
            Step(
                "确认地址池还有空闲地址",
                {"h3c": ["display dhcp server ip-in-use", "display dhcp server pool"],
                 "cisco": ["show ip dhcp pool", "show ip dhcp binding"],
                 "ruijie": ["show ip dhcp pool"]},
                "看已用数量和池子总大小。用满了就是拿不到，和网络无关。",
                "池子耗尽 -> 扩网段，或者查是不是有终端在刷地址。"),
            Step(
                "确认请求真的到了服务器（中继场景）",
                {"h3c": ["display dhcp relay statistics"],
                 "cisco": ["show ip dhcp snooping", "show ip helper-address"],
                 "ruijie": ["show ip dhcp snooping"]},
                "中继场景下要确认 relay 的 server-address 配对，"
                "以及 VLAN 接口上配了 dhcp select relay。",
                "请求数为 0 -> 二层就没把 DHCP 报文送上来，回到 VLAN 排查。"),
            Step(
                "确认不是被 Snooping 或端口安全拦了",
                {"h3c": ["display dhcp snooping statistics", "display port-security"],
                 "cisco": ["show ip dhcp snooping", "show port-security"],
                 "ruijie": ["show ip dhcp snooping", "show port-security"]},
                "开了 Snooping 但上行口没设 trust，DHCP 回复会被直接丢弃 —— "
                "这个现象和「服务器没响应」完全一样，但原因在交换机。",
                "上行口 trusted 没配 -> 补上。"),
        ),
        pitfalls=(
            "Windows 的「169.254」是自动私有地址，说明一个 DHCP Offer 都没收到。",
            "地址池网段和 VLAN 接口网段不一致，服务器会静默不回。",
        ),
    ),
    Case(
        key="ospf_peer",
        name="OSPF 邻居起不来",
        symptom="两台上联设备之间 OSPF 邻居一直卡在 Init / ExStart / Down。",
        steps=(
            Step(
                "看邻居状态和邻居表",
                {"h3c": ["display ospf peer"],
                 "cisco": ["show ip ospf neighbor"],
                 "ruijie": ["show ip ospf neighbor"]},
                "Full 才是正常。Down 是根本没收到 hello；Init 是收到了但对面没收到我的；"
                "ExStart / Exchange 卡住通常是 MTU 不一致。",
                "按状态往下走，别一概而论。"),
            Step(
                "确认两边接口都宣告进了同一个 area",
                {"h3c": ["display ospf interface"],
                 "cisco": ["show ip ospf interface brief"],
                 "ruijie": ["show ip ospf interface brief"]},
                "接口要出现在 OSPF 接口列表里，Area 要一致。",
                "接口没进 OSPF -> 补 network 宣告。Area 不一致 -> 邻居起不来是必然的。"),
            Step(
                "确认 hello / dead 定时器一致",
                {"h3c": ["display ospf interface"],
                 "cisco": ["show ip ospf interface"],
                 "ruijie": ["show ip ospf interface"]},
                "两端 Hello 和 Dead 必须完全一样（默认 10 / 40 秒）。"
                "一端改过就会永远停在 Down。",
                "不一致 -> 统一回默认值。"),
            Step(
                "确认 MTU 一致（ExStart 卡住的典型原因）",
                {"h3c": ["display interface <接口>"],
                 "cisco": ["show interfaces <接口>"],
                 "ruijie": ["show interfaces <接口>"]},
                "MTU 不一样时邻居能到 ExStart，但 DD 报文交换不下去。"
                "这个状态很迷惑人，因为 ping 是通的。",
                "MTU 不一致 -> 两边改成一样，或临时 ip ospf mtu-ignore。"),
            Step(
                "确认认证配置一致",
                {"h3c": ["display ospf interface"],
                 "cisco": ["show ip ospf interface"],
                 "ruijie": ["show ip ospf interface"]},
                "一端开了 MD5 认证另一端没开，邻居会停在 Down，"
                "日志里通常只说「认证失败」。",
                "认证不一致 -> 统一认证方式和密钥。"),
        ),
        pitfalls=(
            "二层不通但 ping 得通的情况不存在 —— OSPF 起不来先确认直连能 ping。",
            "同一网段起了两个 OSPF 进程，容易互相干扰。",
        ),
    ),
    Case(
        key="port_up_no_traffic",
        name="端口 UP 但业务不通",
        symptom="接口指示灯亮、状态是 UP，但终端上不了网，也 ping 不通网关。",
        steps=(
            Step(
                "确认端口有没有学到 MAC",
                {"h3c": ["display mac-address interface <接口>"],
                 "cisco": ["show mac address-table interface <接口>"],
                 "ruijie": ["show mac-address-table interface <接口>"]},
                "有 MAC 说明二层通了，问题在三层或策略。没有 MAC 说明报文根本没上来。",
                "没 MAC -> 查网线、光模块、终端网卡。"),
            Step(
                "确认端口没有被端口安全 / 802.1X 限制",
                {"h3c": ["display port-security", "display dot1x connection"],
                 "cisco": ["show port-security", "show authentication sessions"],
                 "ruijie": ["show port-security", "show dot1x"]},
                "端口安全触发后端口会进入 restrict / shutdown 状态，"
                "但接口本身可能还显示 UP。",
                "被限制 -> 清掉 sticky MAC 或调整 max-mac-count。"),
            Step(
                "确认端口 VLAN 和终端预期一致",
                {"h3c": ["display vlan brief", "display current-configuration interface <接口>"],
                 "cisco": ["show vlan brief", "show running-config interface <接口>"],
                 "ruijie": ["show vlan", "show running-config interface <接口>"]},
                "接了终端却是 trunk 模式，或者划进了错误的 VLAN，"
                "现象都是「灯亮但不通」。",
                "VLAN 不对 -> 改 port access vlan。"),
            Step(
                "看接口计数有没有错包",
                {"h3c": ["display interface <接口>"],
                 "cisco": ["show interfaces <接口>"],
                 "ruijie": ["show interfaces <接口>"]},
                "重点看 input errors、CRC、runts。CRC 在涨基本是光模块或网线问题。",
                "错包在涨 -> 换线换模块，别在配置上纠缠。"),
        ),
        pitfalls=(
            "err-disable 的端口在有些型号上仍显示 UP，要专门看 errdisable 状态。",
            "双工不匹配时 ping 大包丢、小包通，很容易被当成「网络时好时坏」。",
        ),
    ),
)


def get(key: str):
    for case in CASES:
        if case.key == key:
            return case
    return CASES[0]


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def render(case: Case, vendor_key: str = "h3c", include_header: bool = True) -> list:
    """把一个案例渲染成文本行。

    ``include_header=False`` 用于网页版：那边卡片标题和现象是单独排版渲染的，
    再带上这一段就会重复两遍。
    """
    out: list = []
    if include_header:
        out.append(f"━━ {case.name} ━━")
        out.append("")
        out.append(f"现象：{case.symptom}")
        out.append("")

    for index, step in enumerate(case.steps, 1):
        out.append(f"{index}. {step.check}")
        commands = step.commands_for(vendor_key)
        if commands:
            out.append("   命令：")
            for command in commands:
                out.append(f"     {command}")
        if step.judge:
            for line in _wrap("判读：" + step.judge, 58):
                out.append(f"   {line}")
        if step.then:
            for line in _wrap("往哪走：" + step.then, 58):
                out.append(f"   {line}")
        out.append("")

    if case.pitfalls:
        out.append("━━ 容易踩的坑 ━━")
        out.append("")
        for item in case.pitfalls:
            for line in _wrap("· " + item, 62):
                out.append(f"  {line}")
        out.append("")

    out.append("━━ 说明 ━━")
    out.append("")
    out.append("· 命令按当前选中的厂商给出。H3C 与华为都是 display，思科与锐捷都是 show。")
    out.append("· 排查顺序是按「从下往上、从简单到复杂」排的，不建议跳步。")
    out.append("· 每一步的判读写的是「看到什么该往哪走」，不是「检查是否正确」——")
    out.append("  后者等于没说。")
    return out


def _width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _wrap(text: str, width: int) -> list:
    """按显示宽度折行，**优先在空格处断** —— 否则 show 命令会被切成
    ``switc`` / ``hport`` 这种，读起来反而更费劲。"""
    out: list = []
    cur = ""
    for ch in text:
        if _width(cur) + (2 if ord(ch) > 0x2E80 else 1) > width:
            cut = cur.rfind(" ")
            if cut > width // 3:
                out.append(cur[:cut])
                cur = cur[cut + 1:]
            else:
                out.append(cur)
                cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out
