"""命令正确性核验记录。

这里记录的是「实际做过什么核对、发现并改掉了什么、哪些还需要用户自己确认」。
不写空话，只写能追溯的结论。
"""
from __future__ import annotations

#: 逐条核验的情况说明
CHECKED: tuple = (
    ("H3C Comware V5 / V7",
     "system-view / return / save、vlan + name、port link-type 与 port trunk permit、"
     "Bridge-Aggregation 与 Route-Aggregation、link-aggregation mode dynamic、"
     "stp global enable 与 stp mode、MST 域配置与 stp instance root、stp edged-port、"
     "Vlan-interface 与 ip address、dhcp server ip-pool 系列、ip route-static 与 preference、"
     "acl basic / advanced 与 rule、packet-filter、mirroring-group 三种用法、"
     "local-user class manage 与 line vty、irf 系列、vrrp vrid 系列、track、"
     "port-isolate group 与 port-isolate enable group、port-security 系列（含 "
     "timer autolearn aging / timer disableport / intrusion-mode）、"
     "broadcast-suppression 的 ratio 与 pps 写法、loopback-detection global enable 与 "
     "global action、dhcp snooping enable / trust / check mac-address、"
     "arp detection enable / validate / trust、ip verify source、igmp-snooping 与 querier、"
     "ospf router-id / area network / silent-interface、qos lr、info-center 系列、"
     "tftp put/get、display 巡检命令族"),
    ("华为 VRP V5 / V8",
     "system-view / return / save、vlan + description、port link-type 与 port trunk allow-pass、"
     "Eth-Trunk 与 mode lacp-static、stp mode 与 stp edged-port enable、"
     "MST 域配置与 stp instance root、Vlanif 与 ip address、ip pool 系列、"
     "ip route-static 与 preference、acl number 与 rule、traffic-filter、"
     "observe-port 与 port-mirroring / mirroring、aaa 与 local-user、user-interface vty、"
     "stelnet server enable 与 rsa local-key-pair create、stack slot 与 stack-port、"
     "vrrp vrid 系列（含 track interface ... reduced）、"
     "port-isolate enable group 与 port-isolate l3 enable、port-security enable 与 "
     "max-mac-num / protect-action、storm control 与 storm control action、"
     "loop-detection enable 与 interval-time / mode、dhcp enable 与 dhcp snooping enable / trusted、"
     "arp anti-attack check user-bind、ip source check user-bind、igmp-snooping 与 prompt-leave、"
     "ospf router-id / area network / silent-interface、qos lr、info-center 系列、"
     "tftp put/get、display 巡检命令族"),
    ("思科 IOS 12.x / 15.x",
     "configure terminal / end / write memory、vlan + name、switchport mode 与 "
     "switchport trunk allowed vlan、encapsulation dot1q、channel-group 与 Port-channel、"
     "spanning-tree mode 与 mst configuration、Vlan 接口与 ip address、"
     "ip dhcp pool 系列、ip route 与 ip default-gateway、ip access-list 与 ip access-group、"
     "monitor session、username ... secret 与 crypto key generate rsa、"
     "line vty 与 transport input ssh、standby 与 vrrp 系列、"
     "switchport protected、switchport port-security 系列（maximum / violation / "
     "mac-address sticky / aging time）、storm-control level 与 level pps、"
     "spanning-tree portfast bpduguard default、errdisable recovery、"
     "ip dhcp snooping 系列（含 no information option、database）、"
     "ip arp inspection 与 validate、ip verify source、ip igmp snooping 系列、"
     "router ospf 与 passive-interface、policy-map 与 police / service-policy、"
     "logging 系列、copy running-config tftp://、show 巡检命令族"),
    ("锐捷 RGOS 10.x / 11.x",
     "configure terminal / end / write、vlan + name、switchport trunk allowed vlan add、"
     "port-group 与 AggregatePort、spanning-tree mode 与 mst priority 指定根桥、"
     "Vlan 接口与 ip address、service dhcp 与 ip dhcp pool 系列、ip route 与 AD、"
     "ip access-list 与 ip access-group、monitor session、"
     "username ... password 与 enable service ssh-server、no enable service telnet-server、"
     "switch virtual domain 与 vsl-port、vrrp / standby 系列、"
     "switchport protected 与 protected-ports route-deny、switchport port-security 系列、"
     "storm-control level 与 pps、loopback-detection enable / interval、"
     "spanning-tree bpduguard、ip dhcp snooping 系列、ip arp inspection 系列、"
     "ip igmp snooping 系列、router ospf 与 passive-interface、policy-map 与 police、"
     "logging 与 service timestamps、copy running-config tftp://、show 巡检命令族"),
)

#: 本轮实际修正的错误
FIXED: tuple = (
    ("基础 / 标准 ACL 误带协议关键字",
     "ACL 2000（H3C / 华为）与 ACL 10（思科 / 锐捷）只能匹配源地址，"
     "规则里不允许出现 ip / tcp 协议字段，也不能写目的地址与目的端口。",
     "修正前会生成 rule permit ip source ...（H3C / 华为）与 permit ip ... any（思科 / 锐捷），"
     "设备下发时会直接报语法错误。修正后只保留 source 字段，"
     "并在检测到规则里填了目的字段时明确提示已忽略。"),
    ("ACL 编号区间按厂商拆分",
     "思科 / 锐捷的 100-199 是扩展 ACL，与 H3C / 华为的 2000-2999 基础 ACL "
     "不在同一个编号体系里。",
     "修正前用同一套区间判断，输入 ACL 100 时会被当成标准 ACL，"
     "规则里的协议与端口字段被静默丢弃，策略看起来配上了其实没生效。"
     "修正后按厂商分别判断区间。"),
    ("华为 VLAN 镜像命令写错",
     "S 系列 V200 版本的 VLAN 镜像命令是 mirroring to observe-port <索引> inbound，"
     "只支持镜像 VLAN 收到的报文。",
     "修正前生成的是 mirror to observe-port <索引> both —— 关键字少了 ing，"
     "且 both 超出主流版本支持范围，两条都不对。"
     "修正后统一为 inbound，并提示出方向镜像应改用端口镜像。"),
    ("锐捷 SSH 服务端未开启",
     "锐捷 RGOS 默认关闭 SSH 服务端，必须显式执行 enable service ssh-server。",
     "修正前只配了账号与 RSA 密钥，没有开服务端，SSH 一定连不上。已补上。"),
    ("锐捷关闭 Telnet 的多余命令",
     "RGOS 官方命令是 no enable service telnet-server。",
     "修正前额外生成了一条 no service telnet，该写法在 RGOS 上无对应含义，已移除。"),
    ("时区命令四家写法各不相同",
     "H3C / 华为是 clock timezone <名称> { add | minus } <hh:mm:ss>，"
     "思科 / 锐捷是 clock timezone <名称> <小时> [分钟]；"
     "两边都不接受 UTC+08:00 这种「带符号冒号」的写法。",
     "修正前把界面里填的时区字符串直接原样拼进命令，四家下发都会报错。"
     "修正后先解析成「名称 + 分钟偏移」，再按厂商分别格式化；"
     "解析不出来时跳过并给出提示，而不是生成一条错命令。"),
    ("接口名被空格拆断（OSPF 被动接口）",
     "接口名本身含空格，例如 Vlan-interface 10、GigabitEthernet 0/0/1。",
     "修正前按空白拆分参数，Vlan-interface 10 被劈成两条命令，"
     "生成出 silent-interface Vlan-interface 和 silent-interface 10 两条无效命令。"
     "修正后改为按逗号 / 分号拆分，接口名里的空格原样保留。"),
)

#: 需要用户自行确认的部分
USER_MUST_CHECK: tuple = (
    "端口名与槽位号：不同机型命名差异较大，生成结果里的端口名必须与设备 "
    "show interface / display interface brief 的实际输出逐字一致。",
    "标注了「版本」「部分版本」的命令：这类语法随软件版本变化，"
    "下发前请在目标设备上用 ? 补全确认再执行。",
    "堆叠 / 设备虚拟化：IRF、iStack、StackWise、VSU 的实现差异很大，"
    "本工具给出的是骨架命令，务必对照该机型的手册执行。",
    "地址与参数：接口地址、虚拟网关地址、VLAN 编号等由你填入，"
    "工具不会校验是否与现网冲突。",
    "明文密码：生成结果中的密码为明文，仅用于开局，交付前必须统一更换。",
    "风暴控制 / 环路检测 / IP 源防护 / 静态绑定这几类命令，各家各版本的写法差异明显大于"
    "基础二层命令；生成结果里凡是标注了「部分版本」的地方，都请先在设备上用 ? 补全确认。",
    "思科没有「环路检测（发探测帧）」功能，工具给的是 PortFast + BPDU 保护 + errdisable "
    "自动恢复这套替代方案；如果一定要用发探测帧的方式，需要换厂商或上第三方方案。",
    "工具不生成「静态 IP+MAC 绑定」命令：H3C / 华为 / 思科 / 锐捷在这条上的参数模型都不同，"
    "风险高于收益，只在结果里给出各家模板作为注释。",
    "巡检命令里有几条 show / display 会输出很长（尤其是诊断信息收集），"
    "执行前请打开终端的日志记录功能，否则刷屏内容会丢。",
)


def render_note() -> list:
    """渲染核验说明，供界面与导出使用。"""
    out: list = ["━━ 命令正确性核验说明 ━━", ""]

    out.append("▌核验做了什么")
    out.append("  以四厂商官方命令手册为准，逐条核对语法、关键字、参数顺序与命令所处的视图，")
    out.append("  并重点排查了「同一件事各家写法不同」的跨厂商陷阱（如 H3C 的 access vlan")
    out.append("  与华为的 default vlan、思科的 native vlan 与 H3C 的 pvid）。")
    out.append("")
    for name, scope in CHECKED:
        out.append(f"  · {name}")
        out.append(f"          {scope}")
    out.append("")

    out.append("▌本轮修正的错误")
    for i, (title, why, what) in enumerate(FIXED, start=1):
        out.append(f"  {i}. {title}")
        out.append(f"     原因：{why}")
        out.append(f"     改动：{what}")
    out.append("")

    out.append("▌仍需你在设备上确认的部分（重要）")
    for item in USER_MUST_CHECK:
        out.append(f"  · {item}")
    out.append("")

    out.append("▌边界")
    out.append("  本工具保证「命令拼写与语法结构」正确，不保证「参数取值」适合你的现网。")
    out.append("  配置类操作有风险，请始终在维护窗口内执行，并提前备份设备现有配置。")
    return out
