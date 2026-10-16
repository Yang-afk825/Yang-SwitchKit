"""命令详解知识库。

设计思路：
- 一条「概念」(Doc) 描述一件配置任务，与厂商无关；
- 同一概念在各厂商下的实际书写方式写在 forms 里（正则 + 语法原型 + 版本差异）；
- 于是既能解释生成结果里的每一行命令，也能直接产出《四厂商命令对照表》。

匹配时按正则长度倒序尝试，保证更具体的形式（如 interface Vlan-interface 1）
先于更笼统的形式（如 interface GigabitEthernet1/0/1）命中。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


H = "h3c"
W = "huawei"
C = "cisco"
R = "ruijie"

#: 显示顺序
VENDOR_ORDER: tuple[str, ...] = (H, W, C, R)

#: 详解手册里的分组顺序
GROUP_ORDER: tuple[str, ...] = (
    "入口与保存", "二层", "链路聚合", "生成树", "三层", "安全", "运维", "高可用",
)


@dataclass(frozen=True)
class Form:
    """某厂商对某个概念的具体写法。"""

    pattern: str          # 匹配生成结果中一行的正则
    syntax: str           # 语法原型
    note: str = ""        # 版本 / 平台差异


@dataclass(frozen=True)
class Doc:
    """一条与厂商无关的配置概念。"""

    title: str
    group: str
    desc: str
    params: tuple = ()
    caution: str = ""
    forms: dict = field(default_factory=dict)

    def forms_of(self, vendor_key: str) -> tuple:
        """一个厂商对同一概念可能有多条等价写法，统一取成元组。"""
        value = self.forms.get(vendor_key)
        if value is None:
            return ()
        return value if isinstance(value, tuple) else (value,)

    def syntax_of(self, vendor_key: str) -> str:
        forms = self.forms_of(vendor_key)
        return forms[0].syntax if forms else ""

    def notes_of(self, vendor_key: str) -> tuple:
        return tuple(f.note for f in self.forms_of(vendor_key) if f.note)

    def vendors(self) -> tuple:
        return tuple(v for v in VENDOR_ORDER if v in self.forms)


def F(pattern: str, syntax: str, note: str = "") -> Form:
    return Form(pattern, syntax, note)


def D(title: str, group: str, desc: str, forms: dict,
      params: tuple = (), caution: str = "") -> Doc:
    return Doc(title, group, desc, params, caution, forms)


# ==========================================================================
# 概念库
# ==========================================================================

CATALOG: tuple[Doc, ...] = (

    # ------------------------------------------------------------------
    # 入口与保存
    # ------------------------------------------------------------------
    D("进入系统视图", "入口与保存",
      "从用户视图进入系统视图。所有全局配置都必须先执行这条命令，配置提示符会变成方括号形式。",
      forms={
          H: F(r"^system-view$", "system-view", "Comware V5 / V7 一致。"),
          W: F(r"^system-view$", "system-view", "VRP V5 / V8 一致。"),
          C: F(r"^configure terminal$", "configure terminal", "可简写为 conf t。"),
          R: F(r"^configure terminal$", "configure terminal", "RGOS 支持 conf t 简写。"),
      }),

    D("退回用户视图", "入口与保存",
      "从任意配置视图一步退回用户视图（尖括号提示符），便于执行查看类命令或保存。",
      forms={
          H: F(r"^return$", "return"),
          W: F(r"^return$", "return"),
          C: F(r"^end$", "end"),
          R: F(r"^end$", "end"),
      }),

    D("退出当前视图", "入口与保存",
      "逐级退回上一层视图。批量配置接口时，每个接口配完都要退出来再进下一个。",
      forms={
          H: F(r"^quit$", "quit"),
          W: F(r"^quit$", "quit"),
          C: F(r"^exit$", "exit"),
          R: F(r"^exit$", "exit"),
      }),

    D("保存配置", "入口与保存",
      "把当前运行配置写入启动配置文件。不保存的话，设备重启后所有配置都会丢失。",
      caution="保存是掉电不丢配置的唯一保障。交付前务必确认已保存。",
      forms={
          H: F(r"^save force$", "save force",
               "V7 支持 save force 免确认；V5 用 save 后回车并输入 Y 确认。"),
          W: F(r"^save$", "save", "会提示确认，输入 Y。"),
          C: F(r"^write memory$", "write memory", "可简写 wr；等价于 copy running-config startup-config。"),
          R: (F(r"^write$", "write", "可简写 wr。"),
              F(r"^write memory$", "write memory",
                "等价于 write，把运行配置写入启动配置。")),
      }),

    D("修改设备名", "入口与保存",
      "设置设备的命令行提示符名称。这是开局第一步，也让日志与告警能直接定位到具体设备。",
      params=(("设备名", "1-64 个字符，建议用 位置-角色-编号 三段式，例如 SW-CORE-01。"),),
      caution="改名前先记录原设备名，避免在批量运维环境里失去对设备的标识。",
      forms={
          H: F(r"^sysname\s+\S+$", "sysname <设备名>"),
          W: F(r"^sysname\s+\S+$", "sysname <设备名>"),
          C: F(r"^hostname\s+\S+$", "hostname <设备名>"),
          R: F(r"^hostname\s+\S+$", "hostname <设备名>"),
      }),

    # ------------------------------------------------------------------
    # 二层
    # ------------------------------------------------------------------
    D("创建 VLAN", "二层",
      "创建指定编号的 VLAN 并进入 VLAN 视图，对该 VLAN 的描述、端口划分等操作都在该视图下继续。",
      params=(("vlan-id", "VLAN 编号，取值 1-4094。VLAN 1 是缺省 VLAN，业务上建议避开。"),),
      caution="只能配置已存在的静态 VLAN；三层接口要用的 VLAN 必须先创建。",
      forms={
          H: F(r"^vlan\s+\d+$", "vlan <vlan-id>"),
          W: F(r"^vlan\s+\d+$", "vlan <vlan-id>"),
          C: F(r"^vlan\s+\d+$", "vlan <vlan-id>"),
          R: F(r"^vlan\s+\d+$", "vlan <vlan-id>"),
      }),

    D("名称 / 描述字段", "二层",
      "给 VLAN 或 ACL 起一个可读的名字，只影响可读性，不影响转发。",
      params=(("文本", "1-32 个字符，建议用英文且不带空格，例如 Sales、TO-SERVER。"),),
      caution="思科 / 锐捷的 name 在 MST 配置视图下表示的是域名，不是描述，注意所处的视图。",
      forms={
          H: F(r"^(name|description)\s+\S+$", "name <文本>",
               "VLAN 视图下用 name；ACL 视图下用 description。"),
          W: F(r"^description\s+\S+$", "description <文本>",
               "华为的 VLAN 描述字段是 description，与 H3C / 思科不同。"),
          C: F(r"^(name|remark)\s+\S+$", "name <文本> / remark <文本>",
               "VLAN 视图下用 name，ACL 视图下用 remark。"),
          R: F(r"^(name|remark)\s+\S+$", "name <文本> / remark <文本>",
               "同思科：VLAN 用 name，ACL 用 remark。"),
      }),

    D("进入接口", "二层",
      "进入指定物理接口的接口视图，端口模式、速率、VLAN 等配置都在该视图下完成。",
      params=(("接口名", "必须与设备上 show interface 显示的完整名称一致，含类型前缀与槽位号。"),),
      caution="接口编号写错时设备会提示错误但不会中断业务，仍要认真核对，尤其是万兆口常为 1/0/49 这种编号。",
      forms={
          H: F(r"^interface\s+(?!range|Vlan-interface|Bridge-Aggregation|Route-Aggregation)\S+$",
               "interface <接口名>", "如 GigabitEthernet1/0/1、Ten-GigabitEthernet1/0/49。"),
          W: F(r"^interface\s+(?!range|Vlanif|Eth-Trunk|stack-port)\S+$",
               "interface <接口名>", "如 GigabitEthernet0/0/1、XGigabitEthernet0/0/49。"),
          C: F(r"^interface\s+(?!range|Vlan|Port-channel)\S+$",
               "interface <接口名>", "如 GigabitEthernet1/0/1；2960 等固定机型为 GigabitEthernet0/1。"),
          R: F(r"^interface\s+(?!range|Vlan|AggregatePort)\S+(?:\s+\S+)?$",
               "interface <接口名>", "如 GigabitEthernet 0/1，名称与编号之间有空格。"),
      }),

    D("批量进入接口", "二层",
      "一次进入多个接口，后续命令对所有成员同时生效。批量划分 VLAN 时能显著减少重复劳动。",
      params=(("范围", "起止接口必须同类型、同槽位，且编号连续。"),),
      caution="range 里的接口不能跨槽位、不能跨类型；思科同时限制成员数量上限（部分平台为 24 个）。",
      forms={
          H: F(r"^interface range\s+\S+\s+to\s+\S+$", "interface range <起始口> to <结束口>",
               "连接符是 to。"),
          W: F(r"^interface range\s+\S+\s+to\s+\S+$", "interface range <起始口> to <结束口>",
               "连接符是 to。"),
          C: F(r"^interface range\s+\S+\s+-\s+\S+$",
               "interface range <完整起始口> - <末位编号>", "如 interface range GigabitEthernet1/0/1 - 8。"),
          R: F(r"^interface range\s+\S+\s+\S+$",
               "interface range <起始口>-<末位编号>", "如 interface range GigabitEthernet 0/1-4。"),
      }),

    D("接口关闭 / 开启", "二层",
      "管理接口的物理状态。堆叠、聚合等操作要求成员口先关闭再配置，最后重新开启。",
      caution="对承载业务的端口执行 shutdown 会直接中断该端口所有流量。",
      forms={
          H: F(r"^shutdown$", "shutdown"),
          W: F(r"^shutdown$", "shutdown"),
          C: F(r"^shutdown$", "shutdown"),
          R: F(r"^shutdown$", "shutdown"),
      }),

    D("取消接口关闭", "二层",
      "把被 shutdown 的接口重新启用。思科的三层接口默认是关闭的，必须显式 no shutdown。",
      forms={
          H: F(r"^undo shutdown$", "undo shutdown"),
          W: F(r"^undo shutdown$", "undo shutdown"),
          C: F(r"^no shutdown$", "no shutdown", "思科三层接口默认 down，必须执行后才能通。"),
          R: F(r"^no shutdown$", "no shutdown"),
      }),

    D("端口工作模式", "二层",
      "把端口设置成 Access（连接终端）或 Trunk（连接交换机 / 上行设备）。这是二层端口最基本的一条属性。",
      params=(("access", "接入模式，属于且仅属于一个 VLAN。"),
              ("trunk", "干道模式，可以承载多个 VLAN 的带标签流量。")),
      caution="两端互联的端口模式必须匹配，一端 Access 一端 Trunk 会导致流量不通。",
      forms={
          H: F(r"^port link-type (access|trunk)$", "port link-type {access | trunk}"),
          W: F(r"^port link-type (access|trunk)$", "port link-type {access | trunk}"),
          C: F(r"^switchport mode (access|trunk)$", "switchport mode {access | trunk}"),
          R: F(r"^switchport mode (access|trunk)$", "switchport mode {access | trunk}"),
      }),

    D("Access 口划入 VLAN", "二层",
      "把接入端口的缺省 VLAN 改成业务 VLAN，端口上的终端就归属该 VLAN。",
      params=(("vlan-id", "目标 VLAN，必须已创建。"),),
      forms={
          H: F(r"^port access vlan\s+\d+$", "port access vlan <vlan-id>",
               "H3C 用的是 access vlan 关键字。"),
          W: F(r"^port default vlan\s+\d+$", "port default vlan <vlan-id>",
               "华为叫 default vlan，H3C 叫 access vlan。"),
          C: F(r"^switchport access vlan\s+\d+$", "switchport access vlan <vlan-id>"),
          R: F(r"^switchport access vlan\s+\d+$", "switchport access vlan <vlan-id>"),
      }),

    D("Trunk 放通 VLAN", "二层",
      "指定 Trunk 口允许通过的 VLAN。未放通的 VLAN 报文会被丢弃，即使对端已发送。",
      params=(("VLAN 列表", "支持 10,20,30 与 10 to 20 区间写法；all 表示全部。"),),
      caution="放通全部 VLAN 会把所有广播域铺满整条链路，除非确有必要，否则应写明所需 VLAN。",
      forms={
          H: F(r"^port trunk permit vlan\s+", "port trunk permit vlan <VLAN 列表>",
               "关键字是 permit。"),
          W: F(r"^port trunk allow-pass vlan\s+", "port trunk allow-pass vlan <VLAN 列表>",
               "关键字是 allow-pass，与 H3C 的 permit 不同。"),
          C: F(r"^switchport trunk allowed vlan\s+", "switchport trunk allowed vlan <VLAN 列表>",
               "不带 add 会覆盖原有列表。"),
          R: F(r"^switchport trunk allowed vlan add\s+", "switchport trunk allowed vlan add <VLAN 列表>",
               "锐捷追加放通必须带 add，否则覆盖原有列表。"),
      }),

    D("Trunk 缺省 VLAN（PVID）", "二层",
      "设置 Trunk 口对不打标签报文的归属 VLAN。缺省为 VLAN 1，安全上建议改成专用 VLAN。",
      params=(("vlan-id", "该 Trunk 口的 Native / PVID。"),),
      caution="链路两端的 PVID 必须一致，否则会出现 VLAN 串号。",
      forms={
          H: F(r"^port trunk pvid vlan\s+\d+$", "port trunk pvid vlan <vlan-id>"),
          W: F(r"^port trunk pvid vlan\s+\d+$", "port trunk pvid vlan <vlan-id>"),
          C: F(r"^switchport trunk native vlan\s+\d+$", "switchport trunk native vlan <vlan-id>",
               "思科叫 native vlan。"),
          R: F(r"^switchport trunk native vlan\s+\d+$", "switchport trunk native vlan <vlan-id>"),
      }),

    D("Trunk 封装类型", "二层",
      "指定 Trunk 口使用 802.1Q 封装。部分思科平台支持 ISL 与 802.1Q 两种，必须先指定再切 Trunk。",
      caution="只支持 802.1Q 的平台（如 2960）执行该命令会报错，删掉这一行即可。",
      forms={
          C: F(r"^switchport trunk encapsulation dot1q$", "switchport trunk encapsulation dot1q",
               "必须先于 switchport mode trunk 执行。"),
      }),

    D("查看当前视图配置", "二层",
      "只显示当前所处视图下已生效的配置，用于确认上一条命令是否落盘。配置完立刻 display this 是好习惯。",
      forms={
          H: F(r"^display this$", "display this"),
          W: F(r"^display this$", "display this"),
      }),

    # ------------------------------------------------------------------
    # 链路聚合
    # ------------------------------------------------------------------
    D("创建二层聚合接口", "链路聚合",
      "创建一条逻辑的二层聚合链路，物理成员口加入后共同转发。这是链路聚合的核心对象。",
      params=(("组号", "聚合组编号，两端可不同但建议一致，便于维护。"),),
      caution="聚合口的 VLAN 配置必须与成员口一致，否则成员口无法正常加入转发。",
      forms={
          H: F(r"^interface Bridge-Aggregation\s+\d+$", "interface Bridge-Aggregation <组号>",
               "H3C 二层聚合为 Bridge-Aggregation，三层为 Route-Aggregation。"),
          W: F(r"^interface Eth-Trunk\s+\d+$", "interface Eth-Trunk <组号>"),
          C: F(r"^interface Port-channel\d+$", "interface Port-channel<组号>",
               "思科的聚合口叫 Port-channel，缩写 Po。"),
          R: F(r"^interface AggregatePort\s+\d+$", "interface AggregatePort <组号>",
               "锐捷叫 AggregatePort，缩写 AP。"),
      }),

    D("创建三层聚合接口", "链路聚合",
      "创建三层聚合接口，用于在聚合链路上直接跑 IP。",
      caution="H3C 的三层聚合是 Route-Aggregation，不能与 Bridge-Aggregation 混用。",
      forms={
          H: F(r"^interface Route-Aggregation\s+\d+$", "interface Route-Aggregation <组号>"),
      }),

    D("成员口加入聚合组", "链路聚合",
      "把物理端口加入聚合组。加入后该端口的独立配置会被聚合口接管。",
      params=(("组号", "与聚合接口相同的组号。"),),
      caution="成员口的速率、双工、VLAN 配置必须完全一致；不一致的成员口会被置为 Unselected 状态不转发流量。",
      forms={
          H: F(r"^port link-aggregation group\s+\d+$", "port link-aggregation group <组号>"),
          W: F(r"^eth-trunk\s+\d+$", "eth-trunk <组号>"),
          C: F(r"^channel-group\s+\d+\s+mode\s+\S+$", "channel-group <组号> mode {active | on}"),
          R: F(r"^port-group\s+\d+\s+mode\s+\S+$", "port-group <组号> mode {active | on}",
               "部分老版本不支持 mode 参数，去掉 mode 直接写组号。"),
      }),

    D("动态聚合（LACP）", "链路聚合",
      "启用 LACP 协议协商，两端通过 LACPDU 报文互相确认成员口状态，链路质量异常时能自动剔除成员口。",
      caution="两端必须同为动态模式才能协商成功；一端动态一端静态时静态侧会退化为单口转发。",
      forms={
          H: F(r"^link-aggregation mode dynamic$", "link-aggregation mode dynamic",
               "写在聚合接口视图下。"),
          W: F(r"^mode lacp-static$", "mode lacp-static",
               "华为把动态 LACP 称为 lacp-static（静态 LACP）。"),
      }),

    D("静态聚合", "链路聚合",
      "不跑协商协议，只要成员口 Up 就直接转发。配置简单但缺少链路质量检测。",
      caution="静态聚合无法感知对端状态，对端故障时可能形成黑洞，建议优先用动态 LACP。",
      forms={
          W: F(r"^mode manual load-balance$", "mode manual load-balance"),
      }),

    # ------------------------------------------------------------------
    # 生成树
    # ------------------------------------------------------------------
    D("启用生成树", "生成树",
      "全局开启生成树协议。生成树用于消除二层环路，同时提供链路冗余。",
      caution="生成树关闭时接入环路会在几秒内打满整网带宽，生产环境必须开启。",
      forms={
          H: F(r"^stp global enable$", "stp global enable",
               "Comware V7 为 stp global enable，V5 为 stp enable。"),
          W: F(r"^stp enable$", "stp enable"),
          R: F(r"^spanning-tree$", "spanning-tree"),
      }),

    D("设置生成树模式", "生成树",
      "选择运行 STP、RSTP 还是 MSTP。MSTP 支持多实例，能让不同 VLAN 走不同转发路径，做负载分担。",
      params=(("stp", "标准生成树，收敛慢，不推荐。"),
              ("rstp", "快速生成树，收敛快，单实例。"),
              ("mstp", "多实例生成树，可做 VLAN 级负载分担，园区网主流选择。")),
      caution="全网模式必须统一，混用 MSTP 与 RSTP 会算出与预期不同的拓扑。",
      forms={
          H: F(r"^stp mode \S+$", "stp mode {stp | rstp | mstp}"),
          W: F(r"^stp mode \S+$", "stp mode {stp | rstp | mstp}"),
          C: F(r"^spanning-tree mode \S+$", "spanning-tree mode {pvst | rapid-pvst | mst}",
               "思科默认是 PVST+，配置 MSTP 时对应关键字为 mst。"),
          R: F(r"^spanning-tree mode \S+$", "spanning-tree mode {stp | rstp | mstp}"),
      }),

    D("进入 MST 域配置", "生成树",
      "进入多实例生成树的域配置视图。域名、修订级别、实例与 VLAN 映射三要素必须在同一域内完全一致。",
      caution="三要素任一不同，两台设备就属于不同域，会各自独立计算生成树。",
      forms={
          H: F(r"^stp region-configuration$", "stp region-configuration"),
          W: F(r"^stp region-configuration$", "stp region-configuration"),
          C: F(r"^spanning-tree mst configuration$", "spanning-tree mst configuration"),
          R: F(r"^spanning-tree mst configuration$", "spanning-tree mst configuration"),
      }),

    D("MST 域名", "生成树",
      "设置 MST 域的域名，同域设备的域名必须完全一致（区分大小写）。",
      forms={
          H: F(r"^region-name\s+\S+$", "region-name <域名>"),
          W: F(r"^region-name\s+\S+$", "region-name <域名>"),
      }),

    D("MST 修订级别", "生成树",
      "设置 MST 域的修订级别，同域设备必须一致。修改修订级别会让全网重新收敛。",
      forms={
          H: F(r"^revision-level\s+\d+$", "revision-level <0-65535>"),
          W: F(r"^revision-level\s+\d+$", "revision-level <0-65535>"),
          C: F(r"^revision\s+\d+$", "revision <0-65535>"),
          R: F(r"^revision\s+\d+$", "revision <0-65535>"),
      }),

    D("实例与 VLAN 映射", "生成树",
      "把 VLAN 映射到生成树实例。同一实例内的 VLAN 共用一棵生成树，实例之间可以走不同路径实现负载分担。",
      params=(("实例号", "0-48，实例 0 为默认实例，不能手工映射 VLAN。"),
              ("VLAN 列表", "可写 10,20 或 10 to 20。")),
      caution="未映射的 VLAN 自动归入实例 0，容易造成实例 0 承载绝大部分流量。",
      forms={
          H: F(r"^instance\s+\d+\s+vlan\s+", "instance <实例号> vlan <VLAN 列表>"),
          W: F(r"^instance\s+\d+\s+vlan\s+", "instance <实例号> vlan <VLAN 列表>"),
          C: F(r"^instance\s+\d+\s+vlan\s+", "instance <实例号> vlan <VLAN 列表>"),
          R: F(r"^instance\s+\d+\s+vlan\s+", "instance <实例号> vlan <VLAN 列表>"),
      }),

    D("激活 MST 域配置", "生成树",
      "把域配置真正下发生效。H3C / 华为修改域配置后必须显式激活。",
      caution="激活会触发一次生成树重算，业务可能有秒级抖动。",
      forms={
          H: F(r"^active region-configuration$", "active region-configuration",
               "思科 / 锐捷退出域配置视图即自动生效，无需该命令。"),
          W: F(r"^active region-configuration$", "active region-configuration"),
      }),

    D("指定根桥角色", "生成树",
      "把设备指定为根桥或备份根桥。根桥应放在核心层，接入层设备不要参与抢根。",
      params=(("primary", "主根桥，优先级最低，最优。"),
              ("secondary", "备份根桥，主根故障时接管。")),
      caution="两台设备同时被指定为同实例主根会形成双根，务必只在核心设备上配置。",
      forms={
          H: (F(r"^stp instance\s+\d+\s+root\s+\S+$", "stp instance <实例号> root {primary | secondary}"),
              F(r"^stp root\s+\S+$", "stp root {primary | secondary}",
                "RSTP / STP 模式下不带实例号。")),
          W: (F(r"^stp instance\s+\d+\s+root\s+\S+$", "stp instance <实例号> root {primary | secondary}"),
              F(r"^stp root\s+\S+$", "stp root {primary | secondary}",
                "RSTP / STP 模式下不带实例号。")),
          C: F(r"^spanning-tree mst\s+\d+\s+root\s+\S+$", "spanning-tree mst <实例号> root {primary | secondary}"),
          R: F(r"^spanning-tree mst\s+\d+\s+priority\s+\d+$", "spanning-tree mst <实例号> priority {4096 | 8192}",
               "锐捷用优先级间接指定：主根 4096、备根 8192，数值越小越优先，步长 4096。"),
      }),

    D("边缘端口", "生成树",
      "把连接终端（PC、服务器、打印机）的端口设为边缘端口，不参与拓扑计算，插拔设备不会引起网络震荡。",
      caution="边缘端口绝不能接交换机；建议同时开启 BPDU 保护，收到 BPDU 时自动关闭该端口。",
      forms={
          H: F(r"^stp edged-port$", "stp edged-port"),
          W: F(r"^stp edged-port enable$", "stp edged-port enable",
               "华为多了 enable 关键字，H3C 没有。"),
          C: F(r"^spanning-tree portfast$", "spanning-tree portfast"),
          R: F(r"^spanning-tree portfast$", "spanning-tree portfast"),
      }),

    # ------------------------------------------------------------------
    # 三层
    # ------------------------------------------------------------------
    D("进入三层 VLAN 接口", "三层",
      "进入 VLAN 的三层接口视图。交换机的 VLAN 三层接口就是该网段的网关，需要在上面配 IP 地址。",
      params=(("vlan-id", "必须是已创建的 VLAN。"),),
      caution="VLAN 接口默认是 Up 的，但其下所有物理成员口都不 Up 时，接口会转为 Down，网关不可达。",
      forms={
          H: F(r"^interface Vlan-interface\s+\d+$", "interface Vlan-interface <vlan-id>",
               "H3C 写作 Vlan-interface，中间有连字符。"),
          W: F(r"^interface Vlanif\s+\d+$", "interface Vlanif <vlan-id>",
               "华为写作 Vlanif，是一个连写单词。"),
          C: F(r"^interface Vlan\d+$", "interface Vlan<vlan-id>",
               "思科写作 Vlan10，数字直接跟在后面。"),
          R: F(r"^interface Vlan\s+\d+$", "interface Vlan <vlan-id>",
               "锐捷写作 Vlan 10，中间有空格。"),
      }),

    D("配置接口 IP 地址", "三层",
      "给三层接口配置 IPv4 地址和掩码。掩码可用长度（H3C / 华为）或点分十进制（思科 / 锐捷）。",
      params=(("IP 地址", "本机在该网段的接口地址，通常取网段的第一个可用地址作为网关。"),
              ("掩码", "H3C / 华为写长度如 24；思科 / 锐捷写 255.255.255.0。")),
      caution="同网段不要与其他设备冲突；改地址会让该网段业务短暂中断。",
      forms={
          H: F(r"^ip address\s+\S+\s+\d+$", "ip address <IP 地址> <掩码长度>"),
          W: F(r"^ip address\s+\S+\s+\d+$", "ip address <IP 地址> <掩码长度>"),
          C: F(r"^ip address\s+\S+\s+\S+$", "ip address <IP 地址> <点分掩码>"),
          R: F(r"^ip address\s+\S+\s+\S+$", "ip address <IP 地址> <点分掩码>"),
      }),

    D("配置静态路由", "三层",
      "手工指定到目标网段的下一跳。适用于网络规模小、拓扑固定的场景，也是浮动路由的基础。",
      params=(("目标网段", "形如 192.168.100.0，需与掩码配合。"),
              ("下一跳", "必须是本机直连的可达地址，否则路由不会生效。"),
              ("优先级", "选填。H3C / 华为叫 preference，思科 / 锐捷是管理距离 AD。")),
      caution="H3C / 华为默认优先级 60，思科 / 锐捷默认 AD 1；两者都是数值越大越不优先。做备份路由时给备份路径填更大的值。",
      forms={
          H: F(r"^ip route-static\s+(?!0\.0\.0\.0\s+0\s)\S+\s+\d+\s+\S+",
               "ip route-static <目标网段> <掩码长度> <下一跳> [preference <值>]"),
          W: F(r"^ip route-static\s+(?!0\.0\.0\.0\s+0\s)\S+\s+\d+\s+\S+",
               "ip route-static <目标网段> <掩码长度> <下一跳> [preference <值>]"),
          C: F(r"^ip route\s+(?!0\.0\.0\.0\s+0\.0\.0\.0\s)\S+\s+\S+\s+\S+",
               "ip route <目标网段> <点分掩码> <下一跳> [管理距离]"),
          R: F(r"^ip route\s+(?!0\.0\.0\.0\s+0\.0\.0\.0\s)\S+\s+\S+\s+\S+",
               "ip route <目标网段> <点分掩码> <下一跳> [管理距离]"),
      }),

    D("配置默认路由", "三层",
      "把所有未知目的地的流量交给指定下一跳，相当于指明出口。出口即上行口或防火墙。",
      caution="默认路由写错会让设备成为黑洞，配完务必 ping 外网验证。",
      forms={
          H: F(r"^ip route-static\s+0\.0\.0\.0\s+0\s+\S+$", "ip route-static 0.0.0.0 0 <下一跳>"),
          W: F(r"^ip route-static\s+0\.0\.0\.0\s+0\s+\S+$", "ip route-static 0.0.0.0 0 <下一跳>"),
          C: F(r"^ip route\s+0\.0\.0\.0\s+0\.0\.0\.0\s+\S+$", "ip route 0.0.0.0 0.0.0.0 <下一跳>"),
          R: F(r"^ip route\s+0\.0\.0\.0\s+0\.0\.0\.0\s+\S+$", "ip route 0.0.0.0 0.0.0.0 <下一跳>"),
      }),

    D("配置二层交换机默认网关", "三层",
      "给二层交换机配置缺省网关。二层交换机没有路由表，只能用这条命令指定出口。",
      caution="三层交换机不要用这条命令，应改用 ip route 0.0.0.0 0.0.0.0 配默认路由。",
      forms={
          C: F(r"^ip default-gateway\s+\S+$", "ip default-gateway <网关地址>"),
      }),

    D("查看路由表", "三层",
      "显示设备当前的路由表，用于确认刚下发的路由是否已生效、下一跳是否可达。",
      caution="路由表里有条目不等于能通：还要确认下一跳 ARP 已学习、回程路由存在。",
      forms={
          H: F(r"^display ip routing-table", "display ip routing-table [<网段>] [verbose]"),
          W: F(r"^display ip routing-table", "display ip routing-table [<网段>]"),
          C: F(r"^show ip route", "show ip route [static | summary]"),
          R: F(r"^show ip route", "show ip route [static]"),
      }),

    D("启用 DHCP 服务", "三层",
      "打开设备的 DHCP 服务总开关。不开这个开关，配好的地址池不会对外分配地址。",
      caution="这是最容易被漏掉的一步：地址池配得再对，没开总开关也不发地址。",
      forms={
          H: F(r"^dhcp enable$", "dhcp enable"),
          W: F(r"^dhcp enable$", "dhcp enable"),
          R: F(r"^service dhcp$", "service dhcp", "锐捷的开关命令是 service dhcp。"),
      }),

    D("创建 DHCP 地址池", "三层",
      "创建一个地址池并进入地址池视图，网段、网关、DNS 等参数都在该视图下配置。",
      params=(("池名称", "自定义名称，建议带上 VLAN 信息，如 POOL-VLAN10。"),),
      forms={
          H: F(r"^dhcp server ip-pool\s+\S+$", "dhcp server ip-pool <池名称>"),
          W: F(r"^ip pool\s+\S+$", "ip pool <池名称>"),
          C: F(r"^ip dhcp pool\s+\S+$", "ip dhcp pool <池名称>"),
          R: F(r"^ip dhcp pool\s+\S+$", "ip dhcp pool <池名称>"),
      }),

    D("地址池网段", "三层",
      "指定地址池可分配的网段范围，只有落在该网段内的地址才会被分配出去。",
      caution="网段必须与对应三层接口的地址在同一网段，否则思科/锐捷不会自动关联接口。",
      forms={
          H: F(r"^network\s+\S+\s+mask\s+\S+$", "network <网段> mask <点分掩码>"),
          W: F(r"^network\s+\S+\s+mask\s+\S+$", "network <网段> mask <点分掩码>"),
          C: F(r"^network\s+\S+\s+\S+$", "network <网段> <点分掩码>"),
          R: F(r"^network\s+\S+\s+\S+$", "network <网段> <点分掩码>"),
      }),

    D("下发默认网关", "三层",
      "告诉客户端使用哪个地址作为默认网关。通常就是该网段的三层接口地址。",
      forms={
          H: F(r"^gateway-list\s+", "gateway-list <网关地址>"),
          W: F(r"^gateway-list\s+", "gateway-list <网关地址>"),
          C: F(r"^default-router\s+", "default-router <网关地址>"),
          R: F(r"^default-router\s+", "default-router <网关地址>"),
      }),

    D("下发 DNS 服务器", "三层",
      "把 DNS 服务器地址下发给客户端，否则终端能上网却解析不了域名。",
      forms={
          H: F(r"^dns-list\s+", "dns-list <地址> [<地址> ...]"),
          W: F(r"^dns-list\s+", "dns-list <地址> [<地址> ...]"),
          C: F(r"^dns-server\s+", "dns-server <地址> [<地址> ...]"),
          R: F(r"^dns-server\s+", "dns-server <地址> [<地址> ...]"),
      }),

    D("排除地址", "三层",
      "把网段内某些地址从可分配范围中排除，留给网关、服务器、打印机等需要固定地址的设备。",
      caution="忘记排除网关地址时，DHCP 可能把网关地址分给某个终端，导致整个网段 IP 冲突。",
      forms={
          H: F(r"^forbidden-ip\s+", "forbidden-ip <起始地址> [<结束地址>]",
               "H3C 叫 forbidden-ip，一条最多写 64 个地址。"),
          W: F(r"^excluded-ip-address\s+", "excluded-ip-address <起始地址> [<结束地址>]"),
          C: F(r"^ip dhcp excluded-address\s+", "ip dhcp excluded-address <起始地址> [<结束地址>]",
               "在全局视图下配置，不进入地址池。"),
          R: F(r"^ip dhcp excluded-address\s+", "ip dhcp excluded-address <起始地址> [<结束地址>]"),
      }),

    D("接口引用地址池", "三层",
      "指定该三层接口使用哪个地址池分配地址。",
      forms={
          H: F(r"^dhcp server apply ip-pool\s+\S+$", "dhcp server apply ip-pool <池名称>"),
          W: F(r"^dhcp select global$", "dhcp select global"),
      }),

    D("启用 DHCP 中继", "三层",
      "把接口收到的 DHCP 请求转发给外部服务器。跨网段部署集中式 DHCP 服务器时使用。",
      caution="中继模式下本机不建地址池；服务器一侧还要有到该网段的路由，否则地址发了回不来。",
      forms={
          H: F(r"^dhcp select relay$", "dhcp select relay"),
          W: F(r"^dhcp select relay$", "dhcp select relay"),
      }),

    D("指定中继目标服务器", "三层",
      "指明把 DHCP 请求转发到哪台服务器。可以配多台做冗余。",
      forms={
          H: F(r"^dhcp relay server-address\s+", "dhcp relay server-address <服务器地址>"),
          W: F(r"^dhcp relay server-ip\s+", "dhcp relay server-ip <服务器地址>",
               "华为关键字是 server-ip，H3C 是 server-address。"),
          C: F(r"^ip helper-address\s+", "ip helper-address <服务器地址>",
               "思科用通用中继命令，默认会转发 UDP 67/68 及若干其他端口。"),
          R: F(r"^ip helper-address\s+", "ip helper-address <服务器地址>"),
      }),

    # ------------------------------------------------------------------
    # 安全
    # ------------------------------------------------------------------
    D("创建 ACL", "安全",
      "创建访问控制列表并进入 ACL 视图。ACL 本身不生效，必须被接口或策略引用后才起作用。",
      params=(("基本 / 标准", "只匹配源地址，编号：H3C/华为 2000-2999，思科/锐捷 1-99。"),
              ("高级 / 扩展", "可匹配协议、源目地址与端口，编号：H3C/华为 3000-3999，思科/锐捷 100-199。")),
      caution="编号落在哪个区间就决定它是基础还是高级 ACL，写错编号会得到与预期不同的匹配能力。",
      forms={
          H: F(r"^acl (basic|advanced)\s+\S+$", "acl {basic | advanced} <编号>",
               "Comware V7 语法；V5 为 acl number <编号>。"),
          W: F(r"^acl number\s+\S+$", "acl number <编号>",
               "华为统一用 acl number，靠编号区间区分类型。"),
          C: F(r"^ip access-list (standard|extended)\s+\S+$",
               "ip access-list {standard | extended} <编号或名称>",
               "用名称代替编号时可读性更好，且支持删除单条规则。"),
          R: F(r"^ip access-list (standard|extended)\s+\S+$",
               "ip access-list {standard | extended} <编号或名称>"),
      }),

    D("ACL 规则步长", "安全",
      "设置自动分配规则编号时的递增步长。留出间隔是为了以后能在中间插入新规则而不用重排。",
      caution="修改步长只影响后续自动编号，不会改动已有规则编号。",
      forms={
          H: F(r"^step\s+\d+$", "step <步长>", "华为默认步长 5，思科 / 锐捷默认 10，均无需手工设置。"),
      }),

    D("ACL 规则：按源地址过滤", "安全",
      "基础 / 标准 ACL 的规则，只能匹配源地址，适合做「谁能访问」这类粗粒度控制。",
      params=(("动作", "permit 放行 / deny 拒绝。"),
              ("源地址", "写 网段 + 通配符（反掩码），或 any 表示任意。")),
      caution="基础 / 标准 ACL 里不能出现协议、目的地址、端口字段，写了设备会直接报错。这类 ACL 应尽量部署在靠近目标的位置。",
      forms={
          H: F(r"^rule\s+\d+\s+(permit|deny)\s+source\s+", "rule <编号> {permit | deny} source <网段> <反掩码>",
               "注意：不带协议关键字。"),
          W: F(r"^rule\s+\d+\s+(permit|deny)\s+source\s+", "rule <编号> {permit | deny} source <网段> <反掩码>",
               "注意：不带协议关键字。"),
          C: F(r"^\d+\s+(permit|deny)\s+(\d+\.\d+\.\d+\.\d+|any|host)(\s|$)",
               "<编号> {permit | deny} <源网段> <反掩码> | any | host <IP>",
               "注意：不带协议关键字。"),
          R: F(r"^\d+\s+(permit|deny)\s+(\d+\.\d+\.\d+\.\d+|any|host)(\s|$)",
               "<编号> {permit | deny} <源网段> <反掩码> | any | host <IP>",
               "注意：不带协议关键字。"),
      }),

    D("ACL 规则：按五元组过滤", "安全",
      "高级 / 扩展 ACL 的规则，可同时匹配协议类型、源地址、目的地址和端口号，是精细化策略的主力。",
      params=(("协议", "ip 表示任意协议；更常用 tcp、udp、icmp 以便匹配端口。"),
              ("源 / 目的地址", "写 网段 + 反掩码，或 any。"),
              ("目的端口", "仅 tcp / udp 可用，形如 eq 80、gt 1024、range 1000 2000。")),
      caution="规则自上而下匹配，命中即停。范围大的 permit 放在前面会让后面的 deny 永远不生效。",
      forms={
          H: F(r"^rule\s+\d+\s+(permit|deny)\s+(ip|tcp|udp|icmp)\b",
               "rule <编号> {permit | deny} {ip | tcp | udp | icmp} [source ...] [destination ...] [destination-port ...]"),
          W: F(r"^rule\s+\d+\s+(permit|deny)\s+(ip|tcp|udp|icmp)\b",
               "rule <编号> {permit | deny} {ip | tcp | udp | icmp} [source ...] [destination ...] [destination-port ...]"),
          C: F(r"^\d+\s+(permit|deny)\s+(ip|tcp|udp|icmp)\b",
               "<编号> {permit | deny} {ip | tcp | udp | icmp} <源> <目的> [eq <端口>]"),
          R: F(r"^\d+\s+(permit|deny)\s+(ip|tcp|udp|icmp)\b",
               "<编号> {permit | deny} {ip | tcp | udp | icmp} <源> <目的> [eq <端口>]"),
      }),

    D("接口调用 ACL", "安全",
      "把 ACL 应用到物理接口的某个方向，ACL 从这一刻才开始真正过滤流量。",
      params=(("方向", "inbound 过滤进入接口的流量；outbound 过滤离开接口的流量。"),),
      caution="方向写错会让策略形同虚设，甚至拦掉管理流量把自己关在设备外面，远程操作前先确认。",
      forms={
          H: F(r"^packet-filter\s+\S+\s+(inbound|outbound)$", "packet-filter <ACL 编号> {inbound | outbound}"),
          W: F(r"^traffic-filter\s+(inbound|outbound)\s+acl\s+\S+$",
               "traffic-filter {inbound | outbound} acl <ACL 编号>",
               "华为把方向写在前面，H3C 写在后面。"),
          C: F(r"^ip access-group\s+\S+\s+(in|out)$", "ip access-group <ACL 编号> {in | out}"),
          R: F(r"^ip access-group\s+\S+\s+(in|out)$", "ip access-group <ACL 编号> {in | out}"),
      }),

    # ------------------------------------------------------------------
    # 运维
    # ------------------------------------------------------------------
    D("创建本地镜像组", "运维",
      "建立一个本地的镜像组，用于把某些端口的流量复制给监控口。",
      caution="一个镜像组只能有一个监控口；需要多个监控点时建多个镜像组。",
      forms={
          H: F(r"^mirroring-group\s+\d+\s+local$", "mirroring-group <组号> local"),
      }),

    D("定义监控 / 观察端口", "运维",
      "指定接收镜像流量的端口，抓包主机或 IDS 接在这个口上。",
      caution="监控口不要同时承载业务；该口带宽必须大于被镜像流量的总和，否则会丢包。",
      forms={
          H: F(r"^mirroring-group\s+\d+\s+monitor-port$", "mirroring-group <组号> monitor-port",
               "写在监控口的接口视图下。"),
          W: F(r"^observe-port\s+\d+\s+interface\s+\S+$", "observe-port <索引> interface <接口名>",
               "华为把监控口叫观察端口，需先在系统视图定义再被引用。"),
          C: F(r"^monitor session\s+\d+\s+destination interface\s+\S+(?:\s+\S+)?$",
               "monitor session <会话号> destination interface <接口名>"),
          R: F(r"^monitor session\s+\d+\s+destination interface\s+\S+(?:\s+\S+)?$",
               "monitor session <会话号> destination interface <接口名>"),
      }),

    D("镜像源端口", "运维",
      "指定被镜像的端口。可以是单个口，也可以是一个连续范围。",
      forms={
          H: F(r"^mirroring-group\s+\d+\s+mirroring-port\s+",
               "mirroring-group <组号> mirroring-port {both | inbound | outbound}",
               "写在被镜像端口的接口视图下。"),
          W: F(r"^port-mirroring to observe-port\s+\d+\s+\S+$",
               "port-mirroring to observe-port <索引> {both | inbound | outbound}",
               "写在被镜像端口的接口视图下。"),
          C: F(r"^monitor session\s+\d+\s+source interface\s+",
               "monitor session <会话号> source interface <接口> {rx | tx | both}"),
          R: F(r"^monitor session\s+\d+\s+source interface\s+",
               "monitor session <会话号> source interface <接口> {rx | tx | both}"),
      }),

    D("镜像源 VLAN", "运维",
      "对整个 VLAN 的流量做镜像，适合监控某个业务网段的全部流量。",
      caution="VLAN 镜像对单板转发性能有影响，流量大时可能丢包，且部分平台只支持入方向。",
      forms={
          H: F(r"^mirroring-group\s+\d+\s+mirroring-vlan\s+",
               "mirroring-group <组号> mirroring-vlan <VLAN 列表> {both | inbound | outbound}",
               "较老版本只支持 inbound，写 both 会报错。"),
          W: F(r"^mirroring to observe-port\s+\d+\s+\S+$",
               "mirroring to observe-port <索引> inbound",
               "主流 V200 版本只支持 inbound；部分 CE 系列写作 mirroring observe-port <索引> {inbound | outbound | both}。"),
          C: F(r"^monitor session\s+\d+\s+source vlan\s+",
               "monitor session <会话号> source vlan <VLAN> {rx | tx | both}"),
          R: F(r"^monitor session\s+\d+\s+source vlan\s+",
               "monitor session <会话号> source vlan <VLAN> {rx | tx | both}"),
      }),

    D("进入 AAA 视图", "运维",
      "进入认证、授权、计费配置视图，本地用户与权限策略在这里配置。",
      forms={
          W: F(r"^aaa$", "aaa"),
      }),

    D("创建本地管理员账号", "运维",
      "在设备上创建一个本地账号，用于 SSH / Console 登录。",
      params=(("用户名", "建议不要用 admin，用可追溯到人的命名如 netadmin、ops-zhang。"),
              ("权限级别", "15 为最高权限（华为 / 思科 / 锐捷）；H3C 用角色 network-admin 表达。")),
      caution="生产设备禁止使用默认账号和弱口令，且应按人分配账号而不是共用。",
      forms={
          H: (F(r"^local-user\s+\S+\s+class\s+\S+$", "local-user <用户名> class {manage | network}",
                "Comware V7 语法；V5 没有 class 参数。"),
              F(r"^(service-type|authorization-attribute)\s+",
                "service-type ssh / authorization-attribute user-role <角色>",
                "子命令写在 local-user 视图下，分别指定允许的服务类型与用户角色。")),
          W: F(r"^local-user\s+", "local-user <用户名> {password | privilege | service-type} ..."),
          C: F(r"^username\s+", "username <用户名> privilege <级别> secret <密码>"),
          R: F(r"^username\s+", "username <用户名> privilege <级别> password <密码>"),
      }),

    D("设置账号密码", "运维",
      "为本地账号设置登录密码。密码可以明文下发，也可以交互输入，后者更安全。",
      caution="脚本里的明文密码会留在配置文件中，交付前必须统一更换，并改用加密存储。",
      forms={
          H: F(r"^password\s+\S+\s+\S+$", "password {simple | cipher} <密码>",
               "simple 为明文，cipher 为密文。"),
      }),

    D("配置 VTY 远程登录", "运维",
      "进入远程登录线路视图。VTY 是虚拟终端线路，SSH / Telnet 会话都走这里。",
      params=(("线路范围", "0 4 表示允许 5 个并发会话，按需调整。"),),
      forms={
          H: F(r"^line vty\s+\d+\s+\d+$", "line vty 0 4"),
          W: F(r"^user-interface vty\s+\d+\s+\d+$", "user-interface vty 0 4",
               "华为的视图名是 user-interface。"),
          C: F(r"^line vty\s+\d+\s+\d+$", "line vty 0 4"),
          R: F(r"^line vty\s+\d+\s+\d+$", "line vty 0 4"),
      }),

    D("VTY 认证方式", "运维",
      "指定远程登录使用本地账号库还是 AAA 认证。只有配了认证，账号密码才会被真正校验。",
      caution="认证方式配置不完整会导致登录时提示失败，或退回到无认证状态。",
      forms={
          H: F(r"^authentication-mode\s+\S+$", "authentication-mode scheme",
               "scheme 表示使用本地用户 / AAA 认证。"),
          W: F(r"^authentication-mode\s+\S+$", "authentication-mode aaa"),
          C: F(r"^login local$", "login local", "思科用 login local 指定使用本地账号库。"),
          R: F(r"^login local$", "login local"),
      }),

    D("限制仅 SSH 登录", "运维",
      "只允许 SSH 协议接入，关闭明文 Telnet，是管理面加固最基本的一条。",
      caution="关闭 Telnet 前务必确认 SSH 已可用，否则会失去远程管理通道。",
      forms={
          H: F(r"^protocol inbound ssh$", "protocol inbound ssh"),
          W: F(r"^protocol inbound ssh$", "protocol inbound ssh"),
          C: F(r"^transport input ssh$", "transport input ssh"),
          R: F(r"^transport input ssh$", "transport input ssh"),
      }),

    D("会话空闲超时", "运维",
      "设置远程登录会话空闲多久后自动断开，避免终端长期挂着成为风险点。",
      params=(("10 0", "两个数分别表示 分钟 与 秒，10 0 即 10 分 0 秒。"),),
      caution="设得太短会频繁掉线，太长则留下长期未退出的会话，建议 5-10 分钟。",
      forms={
          C: F(r"^exec-timeout\s+\d+\s+\d+$", "exec-timeout <分钟> <秒>"),
          R: F(r"^exec-timeout\s+\d+\s+\d+$", "exec-timeout <分钟> <秒>"),
      }),

    D("启用 SSH 服务端", "运维",
      "打开设备的 SSH 服务。锐捷默认是关闭的，不执行这一步 SSH 一定连不上。",
      caution="启用后仍需生成主机密钥并配置好账号，SSH 才能真正登录。",
      forms={
          H: F(r"^ssh server enable$", "ssh server enable"),
          W: F(r"^stelnet server enable$", "stelnet server enable",
               "华为把 SSH 服务端称为 stelnet。"),
          R: F(r"^enable service ssh-server$", "enable service ssh-server",
               "锐捷默认关闭 SSH 服务端，必须显式开启。"),
      }),

    D("关闭 Telnet 服务", "运维",
      "关闭设备的 Telnet 服务端，避免口令明文在网络上传输。",
      forms={
          H: F(r"^undo telnet server enable$", "undo telnet server enable"),
          W: F(r"^undo telnet server enable$", "undo telnet server enable"),
          R: F(r"^no enable service telnet-server$", "no enable service telnet-server",
               "锐捷用 no enable service 关闭指定服务。"),
      }),

    D("生成 RSA 主机密钥", "运维",
      "生成 SSH 服务端所需的主机密钥。思科 / 锐捷必须先配好 hostname 与域名，否则命令会失败。",
      caution="密钥生成在低端设备上可能耗时数分钟，期间不要中断。",
      forms={
          W: F(r"^rsa local-key-pair create$", "rsa local-key-pair create",
               "会交互询问密钥长度，建议输入 2048。"),
          C: F(r"^crypto key generate rsa", "crypto key generate rsa modulus 2048",
               "前置条件：已配置 hostname 与 ip domain-name。"),
          R: F(r"^crypto key generate rsa", "crypto key generate rsa",
               "前置条件：已配置 hostname 与 ip domain-name。"),
      }),

    D("配置设备域名", "运维",
      "设置设备的域名。思科 / 锐捷生成 RSA 密钥前必须先有这个域名，否则命令报错。",
      forms={
          C: F(r"^ip domain-name\s+\S+$", "ip domain-name <域名>"),
          R: F(r"^ip domain-name\s+\S+$", "ip domain-name <域名>"),
      }),

    D("指定 SSH 版本", "运维",
      "指定只使用 SSHv2。SSHv1 存在已知安全缺陷，不应再使用。",
      forms={
          C: F(r"^ip ssh version 2$", "ip ssh version 2"),
          R: F(r"^ip ssh version 2$", "ip ssh version 2"),
      }),

    D("关闭 HTTP 管理", "运维",
      "关闭设备的 Web 管理界面，减少被弱口令或 Web 漏洞攻击面。",
      forms={
          C: F(r"^no ip http", "no ip http server / no ip http secure-server"),
      }),

    D("启用 NTP 服务", "运维",
      "开启设备的 NTP 客户端功能。时间不准会让日志、证书和审计记录失去参考价值。",
      forms={
          H: F(r"^ntp-service enable$", "ntp-service enable"),
          W: F(r"^ntp-service enable$", "ntp-service enable"),
      }),

    D("指定 NTP 服务器", "运维",
      "指定上游时间源，设备会向其同步时间。建议配两个以上服务器做冗余。",
      forms={
          H: F(r"^ntp-service unicast-server\s+", "ntp-service unicast-server <服务器地址>"),
          W: F(r"^ntp-service unicast-server\s+", "ntp-service unicast-server <服务器地址>"),
          C: F(r"^ntp server\s+", "ntp server <服务器地址>"),
          R: F(r"^ntp server\s+", "ntp server <服务器地址>"),
      }),

    D("启用 SNMP", "运维",
      "开启 SNMP 代理，使设备可被网管平台采集与告警。",
      forms={
          H: F(r"^snmp-agent$", "snmp-agent"),
          W: F(r"^snmp-agent$", "snmp-agent"),
      }),

    D("指定 SNMP 协议版本", "运维",
      "选择使用 SNMPv2c 还是 v3。v3 支持认证与加密，是唯一推荐在生产使用的版本。",
      caution="SNMPv2c 的团体字以明文传输，容易被嗅探，仅用于内网调试或短期过渡。",
      forms={
          H: F(r"^snmp-agent sys-info version\s+\S+$", "snmp-agent sys-info version {v2c | v3 | all}"),
          W: F(r"^snmp-agent sys-info version\s+\S+$", "snmp-agent sys-info version {v2c | v3 | all}"),
      }),

    D("配置 SNMP 读团体字", "运维",
      "配置只读团体字，网管平台凭它读取设备信息。",
      caution="务必改掉默认的 public / private，否则等于把设备信息对外开放。",
      forms={
          H: F(r"^snmp-agent community read\s+", "snmp-agent community read {simple | cipher} <团体字>"),
          W: F(r"^snmp-agent community read\s+", "snmp-agent community read {simple | cipher} <团体字>"),
          C: F(r"^snmp-server community\s+\S+\s+(RO|ro)$", "snmp-server community <团体字> RO"),
          R: F(r"^snmp-server community\s+\S+\s+(ro|RO)$", "snmp-server community <团体字> ro"),
      }),

    D("配置 SNMP Trap 目的", "运维",
      "指定 Trap 告警的接收地址，设备发生异常时主动上报。",
      forms={
          H: F(r"^snmp-agent target-host trap address\s+", "snmp-agent target-host trap address udp-domain <地址> params securityname <团体字> v2c"),
          W: F(r"^snmp-agent target-host trap address\s+", "snmp-agent target-host trap address udp-domain <地址> params securityname <团体字> v2c"),
          C: F(r"^snmp-server host\s+", "snmp-server host <地址> version 2c <团体字>"),
          R: F(r"^snmp-server host\s+", "snmp-server host <地址> version 2c <团体字>"),
      }),

    D("开启 SNMP Trap 上报", "运维",
      "打开告警主动上报的开关。只配了 Trap 目的地址、没开这个开关时，设备仍然不会上报。",
      forms={
          C: F(r"^snmp-server enable traps$", "snmp-server enable traps"),
          R: F(r"^snmp-server enable traps$", "snmp-server enable traps"),
      }),

    D("SNMP 设备位置标识", "运维",
      "填写设备的物理位置信息，网管平台据此快速定位告警来源。",
      forms={
          C: F(r"^snmp-server location\s+", "snmp-server location <位置描述>"),
      }),

    # ------------------------------------------------------------------
    # 高可用
    # ------------------------------------------------------------------
    D("堆叠成员优先级", "高可用",
      "设置本设备在堆叠中的优先级，数值越大越优先被选为主设备。",
      params=(("优先级", "H3C / 华为 / 锐捷常见范围 1-255，默认值多为 32 或 100；思科 StackWise 为 1-15。"),),
      caution="两台设备优先级相同会增加选举的不确定性，建议明确拉开差距。",
      forms={
          H: F(r"^irf member\s+\d+\s+priority\s+\d+$", "irf member <成员号> priority <优先级>"),
          W: F(r"^stack slot\s+\d+\s+priority\s+\d+$", "stack slot <槽位号> priority <优先级>",
               "槽位号需先用 display stack 确认。"),
          C: F(r"^switch\s+\d+\s+priority\s+\d+$", "switch <堆叠编号> priority <1-15>"),
          R: F(r"^switch\s+\d+\s+priority\s+\d+$", "switch <成员号> priority <优先级>"),
      }),

    D("修改堆叠成员号", "高可用",
      "设定本机在堆叠中的成员编号。两台设备的编号必须不同。",
      caution="修改成员号后必须保存并重启才会生效，会造成业务中断，务必在维护窗口操作。",
      forms={
          H: F(r"^irf member\s+\d+\s+renumber\s+\d+$", "irf member <当前编号> renumber <目标编号>"),
          R: F(r"^switch\s+\d+$", "switch <成员号>",
               "锐捷在 switch virtual domain 视图下用该命令进入指定成员的配置子视图。"),
      }),

    D("重启设备", "高可用",
      "重启设备使堆叠 / 虚拟化配置真正生效。",
      caution="重启会中断该设备承载的全部业务，执行前必须确认配置已保存、业务已切换。",
      forms={
          R: F(r"^reload$", "reload", "锐捷 / 思科用 reload；华为 / H3C 用 reboot。"),
      }),

    D("设置虚拟域号", "高可用",
      "给堆叠 / 虚拟化域编号，避免不同堆叠组之间误合并。",
      caution="同域的两台设备域号必须一致。",
      forms={
          H: F(r"^irf domain\s+\d+$", "irf domain <域号>"),
          R: F(r"^switch virtual domain\s+\d+$", "switch virtual domain <域号>",
               "锐捷的 VSU 用虚拟域组织两台设备。"),
      }),

    D("绑定堆叠物理端口", "高可用",
      "把物理端口指定为堆叠互联口。堆叠链路的可靠性直接决定虚拟化系统的稳定。",
      caution="同一成员的两个堆叠端口不能绑定同一物理口；建议至少两条链路并做跨设备聚合。",
      forms={
          H: (F(r"^irf-port\s+\S+$", "irf-port <成员号>/<端口号>"),
              F(r"^port group interface\s+\S+$", "port group interface <接口名>")),
          W: (F(r"^interface stack-port\s+\S+$", "interface stack-port <槽位>/<端口号>"),
              F(r"^port interface\s+\S+\s+enable$", "port interface <接口名> enable")),
          R: (F(r"^vsl-port$", "vsl-port"),
              F(r"^port-member interface\s+", "port-member interface <接口名>")),
      }),

    D("激活堆叠配置", "高可用",
      "让堆叠端口配置立即生效。执行后主备会重新选举，业务有秒级抖动。",
      forms={
          H: F(r"^irf-port-configuration active$", "irf-port-configuration active",
               "部分版本保存后重启即可生效，无需该命令。"),
      }),

    D("VRRP 虚拟网关 IP", "高可用",
      "配置虚拟路由器的虚拟 IP，它会被两台设备共同持有，终端把它当网关。",
      params=(("组号", "1-255，两台设备必须一致。"),
              ("虚拟 IP", "必须与接口地址同网段，且不能被其它设备占用。")),
      caution="组号、虚拟 IP、认证参数在两台设备上必须完全一致，否则会形成双主，导致地址冲突。",
      forms={
          H: F(r"^vrrp vrid\s+\d+\s+virtual-ip\s+\S+$", "vrrp vrid <组号> virtual-ip <虚拟IP>"),
          W: F(r"^vrrp vrid\s+\d+\s+virtual-ip\s+\S+$", "vrrp vrid <组号> virtual-ip <虚拟IP>"),
          C: F(r"^vrrp\s+\d+\s+ip\s+\S+$", "vrrp <组号> ip <虚拟IP>",
               "思科 / 锐捷不写 vrid 关键字。"),
          R: F(r"^vrrp\s+\d+\s+ip\s+\S+$", "vrrp <组号> ip <虚拟IP>"),
      }),

    D("VRRP 优先级", "高可用",
      "设置本机在 VRRP 组中的优先级，数值越大越优先成为 Master，也就是实际转发流量的那台。",
      params=(("优先级", "范围 1-254，默认 100。主设备建议配 120，备用设备保持默认。"),),
      caution="两台的优先级不要相同，否则会频繁出现角色切换，业务跟着抖动。",
      forms={
          H: F(r"^vrrp vrid\s+\d+\s+priority\s+\d+$", "vrrp vrid <组号> priority <1-254>"),
          W: F(r"^vrrp vrid\s+\d+\s+priority\s+\d+$", "vrrp vrid <组号> priority <1-254>"),
          C: F(r"^vrrp\s+\d+\s+priority\s+\d+$", "vrrp <组号> priority <1-254>"),
          R: F(r"^vrrp\s+\d+\s+priority\s+\d+$", "vrrp <组号> priority <1-254>"),
      }),

    D("VRRP 抢占与延迟", "高可用",
      "开启抢占后，恢复的设备会自动夺回 Master 角色。延迟可避免刚上电还不稳定时就抢主。",
      caution="不配抢占时，即使原主设备恢复，也会保持当前状态直到下一次选举。",
      forms={
          H: F(r"^vrrp vrid\s+\d+\s+preempt-mode", "vrrp vrid <组号> preempt-mode [delay <秒>]"),
          W: F(r"^vrrp vrid\s+\d+\s+preempt-mode", "vrrp vrid <组号> preempt-mode timer delay <秒>"),
          C: F(r"^vrrp\s+\d+\s+preempt", "vrrp <组号> preempt [delay minimum <秒>]"),
          R: F(r"^vrrp\s+\d+\s+preempt", "vrrp <组号> preempt [delay minimum <秒>]"),
      }),

    D("VRRP 上行链路联动", "高可用",
      "监视上行接口状态，上行链路故障时自动降低本机优先级，触发备机抢占，避免流量打到断链的主设备上。",
      caution="不同版本的 track 语法略有差异，下发前请核对本机手册；建议同时做双向监测。",
      forms={
          H: (F(r"^track\s+\d+\s+interface\s+", "track <项号> interface <接口名>",
                "H3C 需先建 track 项，再在 VRRP 里引用。"),
              F(r"^vrrp vrid\s+\d+\s+track\s+", "vrrp vrid <组号> track <项号> reduced <值>")),
          W: F(r"^vrrp vrid\s+\d+\s+track\s+", "vrrp vrid <组号> track interface <接口名> reduced <值>",
               "华为把联动直接写在 vrrp 命令里，不需要先建 track 项。"),
          C: F(r"^vrrp\s+\d+\s+track\s+", "vrrp <组号> track <接口名> decrement <值>"),
          R: F(r"^vrrp\s+\d+\s+track\s+", "vrrp <组号> track <接口名> decrement <值>"),
      }),

    D("HSRP 网关冗余", "高可用",
      "配置思科私有的热备份路由协议。功能与 VRRP 类似，但只能与思科 / 锐捷设备互通。",
      params=(("组号", "0-255。"), ("虚拟 IP", "终端使用的网关地址。"), ("优先级", "0-255，默认 100。")),
      caution="HSRP 是思科私有协议，跨厂商组网必须统一改用 VRRP。锐捷仅部分型号支持 HSRP。",
      forms={
          C: F(r"^standby\s+\d+\s+", "standby <组号> {ip <虚拟IP> | priority <值> | preempt | track ...}"),
          R: F(r"^standby\s+\d+\s+", "standby <组号> {ip <虚拟IP> | priority <值> | preempt | track ...}"),
      }),

    # ==================================================================
    # 第二批：端口隔离与环路防护
    # ==================================================================

    D("端口隔离 / 保护口", "二层",
      "让同一个 VLAN 里的端口两两不能二层互访，但都能与上行口通信。"
      "接终端的口最常配，比划一堆 ACL 简单得多。",
      params=(("隔离组号", "H3C 1-8，华为 1-64。同组内的端口互相隔离。"),
              ("保护口", "思科 / 锐捷的等价概念，没有组号。")),
      caution="H3C 在 IRF 组网下不支持端口隔离；华为的管理口不支持；"
              "保护口只支持以太网物理口与聚合口。",
      forms={
          H: (F(r"^port-isolate group\s+\d+$", "port-isolate group <组号>",
                "系统视图下先创建隔离组。"),
              F(r"^port-isolate enable group\s+\d+$", "port-isolate enable group <组号>",
                "接口视图下把端口加入隔离组。"),
              F(r"^port-isolate mode\s+", "port-isolate mode { all | l3 }",
                "all = 二层三层都隔离；缺省只隔离二层。")),
          W: F(r"^port-isolate enable group\s+\d+$", "port-isolate enable group <组号>",
               "华为不需要预先创建隔离组，直接在接口下指定。"),
          C: F(r"^switchport protected$", "switchport protected",
               "思科叫保护口，只能挡二层。"),
          R: (F(r"^switchport protected$", "switchport protected"),
              F(r"^protected-ports route-deny$", "protected-ports route-deny",
                "全局命令，开了之后保护口之间连三层也不通，不能按端口单独设置。")),
      }),

    D("环路检测", "二层",
      "设备定时从端口发出探测报文，如果自己收到了自己发的报文，就说明存在环路。"
      "专治傻瓜交换机造成的环路——它们不发 BPDU，生成树管不到。",
      params=(("检测周期", "发探测报文的间隔，越短越灵敏但越耗资源。"),
              ("处理动作", "检测到环路后关端口还是只告警。")),
      caution="环路检测与 ERPS / RRPP / SEP / Smart Link / 生成树等环网协议冲突，"
              "跑了环网协议的接口要先把它关掉。思科没有这个功能，正解是 PortFast + BPDU 保护 + 风暴控制。",
      forms={
          H: (F(r"^loopback-detection global enable", "loopback-detection global enable vlan { <列表> | all }",
                "全局开关。"),
              F(r"^loopback-detection enable vlan", "loopback-detection enable vlan { <列表> | all }",
                "接口级开关。"),
              F(r"^loopback-detection interval-time\s+", "loopback-detection interval-time <秒>",
                "默认 30 秒。"),
              F(r"^loopback-detection (global )?action\s+", "loopback-detection [global] action { block | no-learning | shutdown }",
                "缺省只记日志不动端口，所以必须显式配置动作。")),
          W: (F(r"^loop-detection enable$", "loop-detection enable", "全局开关。"),
              F(r"^loop-detection enable vlan", "loop-detection enable vlan { <列表> | all }",
                "必须先开全局，再开 VLAN。"),
              F(r"^loop-detection interval-time\s+", "loop-detection interval-time <秒>"),
              F(r"^loop-detection mode\s+", "loop-detection mode { block | nolearning | port-shutdown | quitvlan | trap }",
                "部分 V200 版本的关键字不同，下发前用 ? 确认。")),
          R: (F(r"^loopback-detection enable$", "loopback-detection enable",
                "全局开关，不能按端口单独开关。"),
              F(r"^loopback-detection interval\s+", "loopback-detection interval <秒>")),
      }),

    D("BPDU 保护与边缘端口", "生成树",
      "把接终端的口设为边缘端口（PortFast），正常情况下立刻进入转发；"
      "再加 BPDU 保护，一旦这些口收到 BPDU（说明下面接的是交换机，插错了）就立刻把它关掉。",
      caution="边缘端口 + BPDU 保护只配在接终端的口上；配到上行口会导致正常 BPDU 被拦、拓扑算错。",
      forms={
          H: (F(r"^stp bpdu-protection$", "stp bpdu-protection", "全局开启。"),
              F(r"^stp edged-port$", "stp edged-port", "接口下指定为边缘端口。")),
          W: (F(r"^stp bpdu-protection$", "stp bpdu-protection", "全局开启。"),
              F(r"^stp edged-port enable$", "stp edged-port enable", "接口下指定为边缘端口。")),
          C: (F(r"^spanning-tree portfast bpduguard default$", "spanning-tree portfast bpduguard default",
                "全局一条，让所有 PortFast 口都受 BPDU 保护。"),
              F(r"^spanning-tree portfast$", "spanning-tree portfast"),
              F(r"^spanning-tree bpduguard enable$", "spanning-tree bpduguard enable")),
          R: (F(r"^spanning-tree portfast$", "spanning-tree portfast"),
              F(r"^spanning-tree bpduguard enable$", "spanning-tree bpduguard enable")),
      }),

    D("err-disable 自动恢复", "生成树",
      "端口被保护机制关掉后进入 err-disabled 状态，默认不会自己恢复。"
      "这两条让它定时自动重试，避免网络修好了端口还一直关着。",
      caution="恢复间隔不要设得太短，环路没排除就恢复会反复震荡；接入层一般 300 秒以上。",
      forms={
          C: (F(r"^errdisable recovery cause\s+", "errdisable recovery cause <原因>"),
              F(r"^errdisable recovery interval\s+", "errdisable recovery interval <秒>")),
          R: (F(r"^errdisable recovery cause\s+", "errdisable recovery cause <原因>",
                "部分 RGOS 版本没有 errdisable 命令，下发前用 ? 确认。"),
              F(r"^errdisable recovery interval\s+", "errdisable recovery interval <秒>")),
      }),

    # ==================================================================
    # 第二批：端口安全
    # ==================================================================

    D("端口安全总开关", "安全",
      "开启端口安全后，端口下能接入的 MAC 数量与来源都受控，防私接路由器、防 MAC 泛洪。",
      caution="端口安全与 802.1X、MAC 认证不能共存；H3C 开启时端口上的这两项配置会被重置。"
              "安全端口不能做镜像目的口，也不能加入聚合组。",
      forms={
          H: F(r"^port-security enable$", "port-security enable",
                "H3C 是系统视图下的全局开关。"),
          W: F(r"^port-security enable$", "port-security enable",
                "华为没有全局开关，直接在接口下开启。"),
          C: F(r"^switchport port-security$", "switchport port-security",
                "接口下开启；端口必须是 access 或 trunk 模式，不能是 dynamic。"),
          R: F(r"^switchport port-security$", "switchport port-security"),
      }),

    D("端口安全最大 MAC 数", "安全",
      "限制一个端口下同时能有多少个合法 MAC。超过后再来新 MAC 就触发违例动作。"
      "接终端的口一般设 1-3 个。",
      forms={
          H: F(r"^port-security max-mac-count\s+", "port-security max-mac-count <数量>"),
          W: F(r"^port-security max-mac-num\s+", "port-security max-mac-num <数量>"),
          C: F(r"^switchport port-security maximum\s+", "switchport port-security maximum <数量>"),
          R: F(r"^switchport port-security maximum\s+", "switchport port-security maximum <数量>"),
      }),

    D("端口安全违例动作", "安全",
      "MAC 数超限或出现非法报文时怎么处理：只丢包、丢包并告警、还是直接关端口。",
      caution="选 shutdown 的端口会进入 err-disabled，需要人工或 errdisable recovery 才能恢复，"
              "生产环境慎用。",
      forms={
          H: F(r"^port-security intrusion-mode\s+", "port-security intrusion-mode { blockmac | disableport | disableport-temporarily }"),
          W: F(r"^port-security protect-action\s+", "port-security protect-action { protect | restrict | shutdown }"),
          C: F(r"^switchport port-security violation\s+", "switchport port-security violation { protect | restrict | shutdown }"),
          R: F(r"^switchport port-security violation\s+", "switchport port-security violation { protect | restrict | shutdown }"),
      }),

    D("端口安全 MAC 固化", "安全",
      "把端口学到的 MAC 固化成安全地址，端口 down 或设备重启后依然保留，"
      "防止有人换台机器就能接进来。",
      forms={
          H: (F(r"^port-security port-mode\s+", "port-security port-mode autolearn",
                "autolearn：先自动学习，学到上限后再来新 MAC 就触发违例。"),
              F(r"^port-security mac-address security", "port-security mac-address security sticky <MAC> vlan <VLAN>")),
          W: (F(r"^port-security mac-address sticky$", "port-security mac-address sticky",
                "开启粘性学习，学到的地址会自动固化。"),
              F(r"^port-security mac-address\s+", "port-security mac-address <MAC> [vlan <VLAN>]")),
          C: (F(r"^switchport port-security mac-address sticky", "switchport port-security mac-address sticky"),
              F(r"^switchport port-security mac-address\s+", "switchport port-security mac-address <MAC> [vlan <VLAN>]")),
          R: (F(r"^switchport port-security mac-address sticky", "switchport port-security mac-address sticky"),
              F(r"^switchport port-security mac-address\s+", "switchport port-security mac-address <MAC> [vlan <VLAN>]")),
      }),

    D("端口安全老化与定时器", "安全",
      "设置安全 MAC 的老化时间，以及端口被暂时关闭后多久恢复。",
      forms={
          H: (F(r"^port-security timer autolearn aging\s+", "port-security timer autolearn aging <分钟>"),
              F(r"^port-security timer disableport\s+", "port-security timer disableport <秒>")),
          W: F(r"^port-security aging-time\s+", "port-security aging-time <分钟>"),
          C: F(r"^switchport port-security aging time\s+", "switchport port-security aging time <分钟>"),
          R: F(r"^switchport port-security aging time\s+", "switchport port-security aging time <分钟>"),
      }),

    # ==================================================================
    # 第二批：风暴抑制
    # ==================================================================

    D("风暴抑制（广播 / 组播 / 未知单播）", "安全",
      "给端口设一个流量上限，某一类报文超过阈值后，超出的部分被直接丢弃，"
      "避免广播风暴把整台设备的 CPU 和带宽打满。",
      params=(("阈值", "按带宽百分比（0-100）或按包速率（pps）。"),
              ("低阈值", "华为必须同时给高低两个值：低于低阈值恢复正常转发，高于高阈值开始抑制。")),
      caution="同一类型报文不要同时配风暴抑制与接口流量阈值，会冲突；"
              "pps / kbps 模式下设备会按芯片步长取整，实际生效值可能与填写值略有出入。",
      forms={
          H: (F(r"^broadcast-suppression\s+", "broadcast-suppression { ratio <百分比> | pps <包数> | kbps <速率> }",
                "百分比关键字是 ratio（不是 percent）。"),
              F(r"^multicast-suppression\s+", "multicast-suppression { ratio <百分比> | pps <包数> }"),
              F(r"^unicast-suppression\s+", "unicast-suppression { ratio <百分比> | pps <包数> }")),
          W: (F(r"^storm control ", "storm control { broadcast | multicast | unknown-unicast } min-rate <低> max-rate <高>",
                "必须同时给低阈值和高阈值，只给一个会报错。部分 VRP V5 版本写作 storm-control（带连字符）。"),
              F(r"^storm control action\s+", "storm control action { block | error-down | suppress }")),
          C: (F(r"^storm-control broadcast\s+", "storm-control broadcast level { <百分比> | pps <包数> }"),
              F(r"^storm-control multicast\s+", "storm-control multicast level { <百分比> | pps <包数> }"),
              F(r"^storm-control unicast\s+", "storm-control unicast level { <百分比> | pps <包数> }")),
          R: (F(r"^storm-control broadcast\s+", "storm-control broadcast { level <百分比> | pps <包数> }",
                "锐捷所有端口出厂默认就有 10% 的抑制，没有特殊需求可以不动。"),
              F(r"^storm-control multicast\s+", "storm-control multicast { level <百分比> | pps <包数> }"),
              F(r"^storm-control unicast\s+", "storm-control unicast { level <百分比> | pps <包数> }")),
      }),

    # ==================================================================
    # 第二批：DHCP Snooping / DAI / IPSG
    # ==================================================================

    D("DHCP 功能总开关", "安全",
      "开启设备的 DHCP 相关功能。DHCP Snooping、DAI、IP 源防护都以它为前置条件。",
      caution="漏了这条，后面的 DHCP Snooping 配了也不生效。",
      forms={
          W: F(r"^dhcp enable$", "dhcp enable"),
          H: F(r"^dhcp enable$", "dhcp enable"),
      }),

    D("DHCP Snooping 全局开关", "安全",
      "监听 DHCP 报文，建立「IP-MAC-VLAN-端口」绑定表，并丢弃来自非信任端口的 DHCP 应答，"
      "挡住私接路由器发地址。",
      caution="只开 Snooping 不指定信任端口，等于把所有 DHCP 应答都丢掉，客户端拿不到地址。",
      forms={
          H: (F(r"^dhcp snooping enable$", "dhcp snooping enable"),
              F(r"^dhcp snooping check mac-address$", "dhcp snooping check mac-address",
                "校验 DHCP 请求里的客户端 MAC 与报文源 MAC 是否一致。")),
          W: F(r"^dhcp snooping enable$", "dhcp snooping enable",
               "全局开关，还要在 VLAN 视图下再开一次。"),
          C: (F(r"^ip dhcp snooping$", "ip dhcp snooping"),
              F(r"^ip dhcp snooping vlan\s+", "ip dhcp snooping vlan <VLAN列表>"),
              F(r"^no ip dhcp snooping information option$", "no ip dhcp snooping information option",
                "推荐关闭 option 82 插入，否则上游服务器不认会丢包。"),
              F(r"^ip dhcp snooping database\s+", "ip dhcp snooping database <文件路径>",
                "绑定表落盘，设备重启后不丢。")),
          R: (F(r"^ip dhcp snooping$", "ip dhcp snooping"),
              F(r"^ip dhcp snooping vlan\s+", "ip dhcp snooping vlan <VLAN列表>")),
      }),

    D("DHCP Snooping 信任端口", "安全",
      "把接 DHCP 服务器或上行链路的端口设为信任端口。只有信任端口收到的 DHCP 应答才被转发，"
      "其余全部丢弃。",
      caution="上行口漏配信任，全网 DHCP 都会失效。",
      forms={
          H: F(r"^dhcp snooping trust$", "dhcp snooping trust"),
          W: F(r"^dhcp snooping trusted$", "dhcp snooping trusted"),
          C: F(r"^ip dhcp snooping trust$", "ip dhcp snooping trust"),
          R: F(r"^ip dhcp snooping trust$", "ip dhcp snooping trust"),
      }),

    D("动态 ARP 检测（DAI）", "安全",
      "收到 ARP 报文时，拿它的源 IP、源 MAC、VLAN、接口去和绑定表比对，"
      "对不上就丢弃。这是防 ARP 欺骗最有效的一条。",
      caution="DAI 依赖 DHCP Snooping 绑定表。终端如果配的是静态 IP，"
              "必须先在设备上建静态绑定，否则它会直接连不上网。",
      forms={
          H: (F(r"^arp detection enable$", "arp detection enable", "在 VLAN 视图下开启。"),
              F(r"^arp detection validate\s+", "arp detection validate { dst-mac | ip | src-mac }",
                "校验 ARP 报文的 MAC 与 IP 是否自相矛盾。")),
          W: F(r"^arp anti-attack check user-bind enable$",
               "arp anti-attack check user-bind enable",
               "在 VLAN 视图或接口视图下开启，推荐按 VLAN。"),
          C: (F(r"^ip arp inspection vlan\s+", "ip arp inspection vlan <VLAN列表>"),
              F(r"^ip arp inspection validate\s+", "ip arp inspection validate { src-mac | dst-mac | ip }")),
          R: (F(r"^ip arp inspection vlan\s+", "ip arp inspection vlan <VLAN列表>"),
              F(r"^ip arp inspection validate\s+", "ip arp inspection validate { src-mac | dst-mac | ip }")),
      }),

    D("ARP 检测信任端口", "安全",
      "上行口要同时设为 DHCP Snooping 和 ARP 检测的信任口，"
      "来自网关的 ARP 报文不再做检查。",
      forms={
          H: F(r"^arp detection trust$", "arp detection trust"),
          W: F(r"^dhcp snooping trusted$", "dhcp snooping trusted",
               "华为的 DAI 复用 DHCP Snooping 的信任接口设置。"),
          C: F(r"^ip arp inspection trust$", "ip arp inspection trust"),
          R: F(r"^ip arp inspection trust$", "ip arp inspection trust"),
      }),

    D("IP 源防护（IPSG）", "安全",
      "按绑定表校验报文的源 IP 与源 MAC，终端私改 IP 后报文会被直接丢弃，"
      "是「禁止私设 IP」最直接的手段。",
      caution="依赖 DHCP Snooping 绑定表；静态 IP 的终端必须补静态绑定，否则上不了网。",
      forms={
          H: F(r"^ip verify source", "ip verify source ip-address mac-address"),
          W: F(r"^ip source check user-bind enable$", "ip source check user-bind enable"),
          C: (F(r"^ip verify source$", "ip verify source"),
              F(r"^ip verify source port-security$", "ip verify source port-security",
                "再叠一层端口安全绑定表的校验。")),
      }),

    # ==================================================================
    # 第二批：组播
    # ==================================================================

    D("IGMP Snooping 开关", "三层",
      "让交换机听懂 IGMP 报文，组播流只往有接收者的端口转发，而不是在 VLAN 里泛洪。"
      "IPTV、视频会议场景必配。",
      forms={
          H: (F(r"^igmp-snooping$", "igmp-snooping", "全局开启。"),
              F(r"^igmp-snooping enable$", "igmp-snooping enable", "在 VLAN 视图下开启。")),
          W: (F(r"^igmp-snooping enable$", "igmp-snooping enable",
                "全局与 VLAN 视图下都是这条命令。"),),
          C: (F(r"^ip igmp snooping$", "ip igmp snooping"),
              F(r"^ip igmp snooping vlan\s+", "ip igmp snooping vlan <VLAN号>")),
          R: (F(r"^ip igmp snooping$", "ip igmp snooping"),
              F(r"^ip igmp snooping vlan\s+", "ip igmp snooping vlan <VLAN号>")),
      }),

    D("IGMP 查询器", "三层",
      "VLAN 内没有三层组播设备时，由交换机自己定期发查询报文，"
      "维持组播成员表项不被老化掉。",
      caution="同一个 VLAN 里只应存在一个查询器，多台都开容易互相干扰。",
      forms={
          H: (F(r"^igmp-snooping querier enable$", "igmp-snooping querier enable"),
              F(r"^igmp-snooping querier source-address\s+", "igmp-snooping querier source-address <IP>",
                "查询报文的源地址，建议用该 VLAN 的网关地址。")),
          W: (F(r"^igmp-snooping querier enable$", "igmp-snooping querier enable"),
              F(r"^igmp-snooping querier source-address\s+", "igmp-snooping querier source-address <IP>")),
          C: F(r"^ip igmp snooping querier", "ip igmp snooping querier",
               "思科按最小 IP 自动选举查询器。"),
          R: F(r"^ip igmp snooping querier", "ip igmp snooping querier"),
      }),

    D("静态指定路由器端口", "三层",
      "指明组播流从哪个口进来（路由器端口）。不指定的话靠动态学习识别，"
      "上行链路抖动时表项容易丢。",
      forms={
          H: F(r"^igmp-snooping router-port interface\s+", "igmp-snooping router-port interface <接口名>"),
          W: F(r"^igmp-snooping router-port interface\s+", "igmp-snooping router-port interface <接口名>"),
          C: F(r"^ip igmp snooping vlan\s+\d+\s+mrouter\s+", "ip igmp snooping vlan <VLAN号> mrouter interface <接口名>"),
          R: F(r"^ip igmp snooping vlan\s+\d+\s+mrouter\s+", "ip igmp snooping vlan <VLAN号> mrouter interface <接口名>"),
      }),

    D("组播快速离开", "三层",
      "收到 IGMP 离开报文后立刻停止向该端口转发组播流，而不是等查询周期结束，"
      "能明显减少换台延迟。",
      caution="一个端口只接一台终端时才建议开；端口下挂多台设备会误伤其他还在收的终端。",
      forms={
          H: F(r"^igmp-snooping fast-leave$", "igmp-snooping fast-leave"),
          W: F(r"^igmp-snooping prompt-leave$", "igmp-snooping prompt-leave",
               "华为把快速离开叫 prompt-leave。"),
          C: F(r"^ip igmp snooping vlan\s+\d+\s+immediate-leave$", "ip igmp snooping vlan <VLAN号> immediate-leave"),
          R: F(r"^ip igmp snooping vlan\s+\d+\s+immediate-leave$", "ip igmp snooping vlan <VLAN号> immediate-leave"),
      }),

    # ==================================================================
    # 第二批：OSPF
    # ==================================================================

    D("启用 OSPF 并指定 Router ID", "三层",
      "打开 OSPF 进程并手工指定 Router ID。Router ID 是 OSPF 用来标识设备的，"
      "建议用手工指定的 Loopback 地址，别让它自动选举。",
      caution="Router ID 冲突会导致邻居反复震荡。",
      forms={
          H: (F(r"^ospf\s+\d+\s+router-id\s+", "ospf <进程号> router-id <IP>"),
              F(r"^ospf\s+\d+$", "ospf <进程号>", "进入 OSPF 进程视图。")),
          W: (F(r"^ospf\s+\d+\s+router-id\s+", "ospf <进程号> router-id <IP>"),
              F(r"^ospf\s+\d+$", "ospf <进程号>", "进入 OSPF 进程视图。")),
          C: (F(r"^ip routing$", "ip routing", "三层交换机必须先打开路由功能。"),
              F(r"^router ospf\s+", "router ospf <进程号>"),
              F(r"^router-id\s+", "router-id <IP>")),
          R: (F(r"^ip routing$", "ip routing"),
              F(r"^router ospf\s+", "router ospf <进程号>"),
              F(r"^router-id\s+", "router-id <IP>")),
      }),

    D("OSPF 宣告网段", "三层",
      "把本机的直连网段加进 OSPF。只有被宣告的网段才会参与 OSPF，"
      "并从对应接口发出 Hello 报文。",
      params=(("网段", "目标网段"), ("区域", "0 是骨干区域，其他区域必须与它相连。")),
      caution="H3C / 华为在 area 视图下宣告，思科 / 锐捷在 router 视图下一条带区域号；"
              "两边都用反掩码。",
      forms={
          H: (F(r"^area\s+\S+$", "area <区域号>"),
              F(r"^network\s+", "network <网段> <反掩码>")),
          W: (F(r"^area\s+\S+$", "area <区域号>"),
              F(r"^network\s+", "network <网段> <反掩码>")),
          C: F(r"^network\s+\S+\s+\S+\s+area\s+", "network <网段> <反掩码> area <区域号>"),
          R: F(r"^network\s+\S+\s+\S+\s+area\s+", "network <网段> <反掩码> area <区域号>"),
      }),

    D("OSPF 区域验证", "三层",
      "给指定区域打开 MD5 验证，防止未授权的设备冒充 OSPF 邻居。"
      "还要在每个跑 OSPF 的接口上配置一致的密钥。",
      caution="区域验证要在同一区域的所有设备上保持一致，密钥不一致或漏配都会导致邻居建立失败。",
      forms={
          H: F(r"^authentication-mode\s+", "authentication-mode md5"),
          W: F(r"^authentication-mode\s+", "authentication-mode md5"),
          C: F(r"^area\s+\S+\s+authentication\s+", "area <区域号> authentication message-digest"),
          R: F(r"^area\s+\S+\s+authentication\s+", "area <区域号> authentication message-digest"),
      }),

    D("OSPF 被动接口", "三层",
      "指定某些接口不发送 OSPF Hello 报文，但网段照样宣告出去。"
      "接终端的接口配成被动，可以省资源也更安全。",
      forms={
          H: F(r"^silent-interface\s+", "silent-interface <接口名>"),
          W: F(r"^silent-interface\s+", "silent-interface <接口名>"),
          C: (F(r"^passive-interface\s+", "passive-interface <接口名>"),
              F(r"^passive-interface default$", "passive-interface default",
                "全局把接口默认设为被动，再对个别接口用 no passive-interface 放开。")),
          R: F(r"^passive-interface\s+", "passive-interface <接口名>"),
      }),

    # ==================================================================
    # 第二批：限速
    # ==================================================================

    D("端口限速（qos lr）", "运维",
      "按接口限制进出流量的速率，用的是承诺信息速率 CIR。"
      "整口统一限速，配置最简单。",
      params=(("CIR", "承诺信息速率，单位 kbps。H3C 要求是 64 的整数倍。"),
              ("CBS", "承诺突发尺寸，单位字节；不给就用设备默认值。")),
      caution="CBS 太小会让 TCP 吞吐骤降，太大又失去限速意义；"
              "10 Mbps 限速一般取 200000 字节左右。",
      forms={
          H: F(r"^qos lr\s+", "qos lr { inbound | outbound } cir <kbps> [ cbs <bytes> ]",
               "CIR 必须是 64 的整数倍。"),
          W: F(r"^qos lr\s+", "qos lr { inbound | outbound } cir <kbps> [ cbs <bytes> ]"),
      }),

    D("限速策略（policy-map + police）", "运维",
      "思科 / 锐捷体系用 QoS 策略做限速：先定义策略，再在端口上应用。"
      "比接口级限速灵活，可以只针对某类流量。",
      params=(("police", "令牌桶速率。思科单位是 bps，锐捷写作 police cir <bps>。"),
              ("conform / exceed", "未超速和超速时分别怎么处理。")),
      caution="策略定义了但没在接口上 service-policy 应用，等于白配。",
      forms={
          C: (F(r"^policy-map\s+", "policy-map <名称>"),
              F(r"^class class-default$", "class class-default"),
              F(r"^police\s+", "police <bps> [<cbs>] conform-action transmit exceed-action drop")),
          R: (F(r"^policy-map\s+", "policy-map <名称>"),
              F(r"^class class-default$", "class class-default"),
              F(r"^police\s+", "police cir <bps> [cbs <bytes>]")),
      }),

    D("应用限速策略", "运维",
      "把限速策略挂到接口上，并指明方向。入方向限的是用户上传，出方向限的是用户下载。",
      forms={
          C: F(r"^service-policy\s+", "service-policy { input | output } <策略名>"),
          R: F(r"^service-policy\s+", "service-policy { input | output } <策略名>"),
      }),

    # ==================================================================
    # 第二批：日志与时钟
    # ==================================================================

    D("日志功能总开关", "运维",
      "打开信息中心，设备才有日志输出。顺带设置本地日志缓冲区能存多少条。",
      caution="没开信息中心时，设备发生什么都不会留下记录。",
      forms={
          H: F(r"^info-center enable$", "info-center enable"),
          W: F(r"^info-center enable$", "info-center enable"),
          R: F(r"^logging on$", "logging on"),
      }),

    D("日志缓冲区", "运维",
      "本地能保存多少条日志。条数越多，出问题时能回溯的窗口越长。",
      forms={
          H: F(r"^info-center logbuffer size\s+", "info-center logbuffer size <条数>"),
          W: F(r"^info-center logbuffer size\s+", "info-center logbuffer size <条数>"),
          C: F(r"^logging buffered\s+", "logging buffered <条数>"),
          R: F(r"^logging buffered\s+", "logging buffered <条数>"),
      }),

    D("日志时间戳", "运维",
      "让日志带上本地时间。没有时间戳的日志在排障时几乎用不上——"
      "你没法判断故障到底是从哪一刻开始的。",
      forms={
          H: (F(r"^info-center timestamp loghost\s+", "info-center timestamp loghost date"),
              F(r"^info-center timestamp log\s+", "info-center timestamp log date"),
              F(r"^info-center format\s+", "info-center format unicom",
                "日志格式，部分版本用 default / cmcc 等取值。")),
          W: (F(r"^info-center timestamp loghost\s+", "info-center timestamp loghost date"),
              F(r"^info-center timestamp log\s+", "info-center timestamp log date")),
          C: F(r"^service timestamps log\s+", "service timestamps log datetime msec localtime [show-timezone]"),
          R: F(r"^service timestamps log\s+", "service timestamps log datetime msec localtime [show-timezone]"),
      }),

    D("日志服务器", "运维",
      "把日志实时送到服务器，设备重启或死机后日志也不会丢，"
      "而且多台设备的日志能集中检索。",
      caution="日志服务器地址和数据网段要能通；配完记得制造一条日志验证是否真的送达。",
      forms={
          H: (F(r"^info-center loghost\s+", "info-center loghost <服务器地址>"),
              F(r"^info-center source\s+", "info-center source <模块> <通道> level <级别>"),
              F(r"^info-center console channel\s+", "info-center console channel console")),
          W: (F(r"^info-center loghost\s+", "info-center loghost <服务器地址>"),
              F(r"^info-center console channel\s+", "info-center console channel console")),
          C: (F(r"^logging host\s+", "logging host <服务器地址>"),
              F(r"^logging trap\s+", "logging trap <级别>"),
              F(r"^logging source-interface\s+", "logging source-interface <接口>",
                "指定日志报文的源地址，便于服务器按设备区分。"),
              F(r"^logging console$", "logging console",
                "把日志同步输出到控制台；思科默认不打，调试时开、平时关。")),
          R: (F(r"^logging server\s+", "logging server <服务器地址>",
                "部分 RGOS 版本写作 logging host。"),
              F(r"^logging trap\s+", "logging trap <级别>"),
              F(r"^logging console$", "logging console")),
      }),

    D("时区", "运维",
      "设置设备所在时区，让日志和时钟显示本地时间。",
      forms={
          H: F(r"^clock timezone\s+", "clock timezone <名称> { add | minus } <hh:mm:ss>"),
          W: F(r"^clock timezone\s+", "clock timezone <名称> { add | minus } <hh:mm:ss>"),
          C: F(r"^clock timezone\s+", "clock timezone <名称> <小时> [分钟]",
               "思科的西区用负数，例如美国东部为 clock timezone EST -5。"),
          R: F(r"^clock timezone\s+", "clock timezone <名称> <小时> [分钟]"),
      }),

    D("NTP 时间同步", "运维",
      "让设备从 NTP 服务器同步时间。时钟不准，日志的先后顺序就不可信，"
      "证书校验也会出问题。",
      forms={
          H: (F(r"^ntp-service enable$", "ntp-service enable"),
              F(r"^ntp-service unicast-server\s+", "ntp-service unicast-server <服务器地址>")),
          W: F(r"^ntp-service unicast-server\s+", "ntp-service unicast-server <服务器地址>",
               "华为从 V200R003 起 NTP 默认开启，一般只需配服务器地址。"),
          C: F(r"^ntp server\s+", "ntp server <服务器地址>"),
          R: F(r"^ntp server\s+", "ntp server <服务器地址>"),
      }),

    # ==================================================================
    # 第二批：备份
    # ==================================================================

    D("配置备份 / 恢复", "运维",
      "把配置传到 TFTP / FTP 服务器，或从服务器取回来。"
      "备份前一定要先保存当前配置，否则传到服务器的是上一次保存过的旧配置。",
      params=(("服务器", "TFTP / FTP 服务器地址。"), ("文件名", "备份文件的名称。")),
      caution="恢复配置会覆盖设备现有配置，需要重启才完全生效，务必在维护窗口内操作。"
              "FTP 方式密码是明文，用完请立刻更换。",
      forms={
          H: (F(r"^tftp\s+", "tftp <服务器> { put | get } <源文件> <目的文件>"),
              F(r"^ftp\s+", "ftp <服务器>，登录后再 put / get")),
          W: (F(r"^tftp\s+", "tftp <服务器> { put | get } <源文件> <目的文件>"),
              F(r"^ftp\s+", "ftp <服务器>，登录后再 put / get")),
          C: F(r"^copy\s+", "copy { running-config | startup-config } <协议>://<服务器>/<文件>"),
          R: F(r"^copy\s+", "copy { running-config | startup-config } <协议>://<服务器>/<文件>"),
      }),

    D("IOS 风格注释行", "入口与保存",
      "以 ! 开头的行是 IOS / RGOS 的注释，设备不会执行，"
      "仅用于在脚本里写说明或分段。",
      forms={
          C: F(r"^!", "! <说明文字>"),
          R: F(r"^!", "! <说明文字>"),
      }),

    # ==================================================================
    # 第二批：巡检命令族
    # ==================================================================

    D("巡检：设备信息与资源", "运维",
      "看设备型号、软件版本、序列号、运行时长，以及 CPU、内存、电源、风扇、温度。"
      "开局先留一份基线，出问题时拿它对比。",
      forms={
          H: F(r"^display (version|device|device manuinfo|device manufacture-info|clock|cpu-usage|memory|memory-usage|boot-loader|startup|fan|power|temperature( all)?)$",
               "display { version | device | clock | cpu-usage | memory | ... }"),
          W: F(r"^display (version|device|device manuinfo|device manufacture-info|clock|cpu-usage|memory|memory-usage|boot-loader|startup|fan|power|temperature( all)?)$",
               "display { version | device | clock | cpu-usage | memory-usage | ... }"),
          C: F(r"^show (version( slots)?|inventory|clock|processes (cpu|memory) sorted|boot|environment( all)?|module|cpu|memory|fan|power)$",
               "show { version | inventory | clock | processes cpu sorted | environment | ... }"),
          R: F(r"^show (version( slots)?|inventory|clock|processes (cpu|memory) sorted|boot|environment( all)?|module|cpu|memory|fan|power)$",
               "show { version | clock | cpu | memory | environment | ... }"),
      }),

    D("巡检：接口与流量", "运维",
      "看接口 up/down、收发速率、错包和丢包计数。"
      "链路质量有问题时，错包计数是第一个要看的数字。",
      forms={
          H: F(r"^display (interface brief|interface|counters|packet-drop|link-delay)",
               "display { interface brief | interface | counters inbound interface | ... }"),
          W: F(r"^display (interface brief|interface|counters)",
               "display { interface brief | interface | counters inbound interface | ... }"),
          C: F(r"^show interfaces( |$)", "show { interfaces status | interfaces | interfaces counters errors | ... }"),
          R: F(r"^show interfaces( |$)", "show { interfaces status | interfaces | interfaces counters errors | ... }"),
      }),

    D("巡检：二层转发表", "运维",
      "看 VLAN、MAC 地址表、聚合口状态、生成树、邻居信息。"
      "MAC 表在两三个端口之间来回跳，基本就是环路或 MAC 漂移。",
      forms={
          H: F(r"^display (vlan|mac-address|link-aggregation|stp|lldp)",
               "display { vlan brief | mac-address | mac-address mac-move | stp brief | ... }"),
          W: F(r"^display (vlan|mac-address|eth-trunk|stp|lldp)",
               "display { vlan | mac-address | mac-address flapping record | eth-trunk | ... }"),
          C: F(r"^show (vlan|mac address-table|etherchannel|spanning-tree|cdp|lldp)",
               "show { vlan brief | mac address-table | etherchannel summary | spanning-tree | ... }"),
          R: F(r"^show (vlan|mac-address-table|interfaces aggregateport|spanning-tree|lldp)",
               "show { vlan | mac-address-table | spanning-tree | lldp neighbors | ... }"),
      }),

    D("巡检：三层与地址", "运维",
      "看路由表、ARP 表、接口地址、DHCP 地址分配、VRRP 状态、OSPF 邻居。"
      "先确认路由表里有没有你要的那条，再看 ARP 学没学到对端。",
      forms={
          H: F(r"^display (ip routing-table|ip interface|arp|dhcp|vrrp|ospf)",
               "display { ip routing-table | arp all | ip interface brief | vrrp brief | ospf peer | ... }"),
          W: F(r"^display (ip routing-table|ip interface|arp|dhcp|vrrp|ospf)",
               "display { ip routing-table | arp all | ip interface brief | vrrp brief | ospf peer | ... }"),
          C: F(r"^show (ip route|ip interface|arp|ip dhcp|standby|ip ospf)",
               "show { ip route | arp | ip interface brief | standby brief | ip ospf neighbor | ... }"),
          R: F(r"^show (ip route|ip interface|arp|ip dhcp|vrrp|ip ospf)",
               "show { ip route | arp | ip interface brief | vrrp brief | ip ospf neighbor | ... }"),
      }),

    D("巡检：安全与接入", "运维",
      "看 DHCP Snooping 绑定表、ARP 检测丢弃计数、端口安全绑定与违例、认证会话、ACL 命中。"
      "命中计数长期为 0 的规则，往往说明它根本没生效。",
      forms={
          H: F(r"^display (dhcp snooping|arp detection|port-security|ip verify|dot1x|mac-address security|acl|packet-filter)",
               "display { dhcp snooping binding | port-security | ip verify source | acl all | ... }"),
          W: F(r"^display (dhcp snooping|arp anti-attack|port-security|ip source check|dot1x|acl|traffic-filter)",
               "display { dhcp snooping binding | port-security | ip source check user-bind | acl all | ... }"),
          C: F(r"^show (ip dhcp snooping|ip arp inspection|port-security|ip verify source|authentication|access-lists|policy-map)",
               "show { ip dhcp snooping binding | ip arp inspection | port-security | access-lists | ... }"),
          R: F(r"^show (ip dhcp snooping|ip arp inspection|port-security|dot1x|acl)",
               "show { ip dhcp snooping binding | ip arp inspection | port-security | acl | ... }"),
      }),

    D("巡检：硬件与光模块", "运维",
      "看光模块的型号、收发功率、告警阈值，以及 PoE 供电和电源状态。"
      "光模块收光功率接近灵敏度下限时，会出现「链路 up 但丢包」的怪现象。",
      forms={
          H: F(r"^display (transceiver|poe|power-supply)",
               "display { transceiver interface | transceiver diagnosis interface | poe interface | ... }"),
          W: F(r"^display (transceiver|poe|power-supply|elabel)",
               "display { transceiver interface | transceiver verbose | poe interface | ... }"),
          C: F(r"^show (interfaces transceiver|power inline|env)",
               "show { interfaces transceiver | power inline | env all | ... }"),
          R: F(r"^show (interfaces transceiver|poe|power-supply)",
               "show { interfaces transceiver | poe interfaces | power-supply | ... }"),
      }),

    D("巡检：日志与告警", "运维",
      "看日志缓冲区、告警、异常重启记录和被 err-disable 的端口。"
      "设备反复重启、端口被保护机制关掉，都能在这里一眼看出来。",
      forms={
          H: F(r"^display (logbuffer|trapbuffer|exception|reboot-info|diag)",
               "display { logbuffer | trapbuffer | exception | reboot-info | diag }"),
          W: F(r"^display (logbuffer|trapbuffer|alarm|reset-reason|cpu-usage history)",
               "display { logbuffer | trapbuffer | alarm active | reset-reason | ... }"),
          C: F(r"^show (logging|archive|reload|errdisable)",
               "show { logging | archive log config all | reload | errdisable recovery | ... }"),
          R: F(r"^show (logging|alarm)",
               "show { logging | logging buffer | alarm | ... }"),
      }),

    D("一键收集诊断信息", "运维",
      "把设备当前的软硬件状态打包成一份综合诊断文件，提交给厂商技术支持时用。",
      caution="这个命令输出很大、耗时可能几分钟，会占用较多 CPU；"
              "执行前先在终端上打开日志记录，否则刷屏的内容会丢。",
      forms={
          H: F(r"^display diagnostic-information$", "display diagnostic-information"),
          W: F(r"^display diagnostic-information$", "display diagnostic-information"),
          C: F(r"^show tech-support$", "show tech-support"),
          R: F(r"^show tech-support$", "show tech-support"),
      }),

    D("核对运行情况", "运维",
      "配置下发之后用来验结果的命令：看限速有没有生效、配置有没有保存、"
      "风暴抑制和端口隔离是不是按预期生效。",
      caution="有些核对命令会输出很长，建议配上 | include 之类的过滤只看关键行。",
      forms={
          H: F(r"^display (qos lr|saved-configuration|info-center|current-configuration|this|port-isolate|loopback-detection|storm)",
               "display { qos lr interface | saved-configuration | info-center | current-configuration | ... }"),
          W: F(r"^display (qos lr|saved-configuration|info-center|current-configuration|this|port-isolate|loop-detection|storm-control)",
               "display { qos lr interface | saved-configuration | info-center | current-configuration | ... }"),
          C: F(r"^show (startup-config|running-config|policy-map|storm-control|controllers)",
               "show { startup-config | running-config | policy-map interface | storm-control | ... }"),
          R: F(r"^show (startup-config|running-config|policy-map|storm-control|protected-ports|loopback-detection|interfaces aggregateport)",
               "show { startup-config | running-config | policy-map interface | storm-control | ... }"),
      }),

    D("带过滤的查询命令", "运维",
      "在查询命令后面用 | 接过滤条件，只显示关心的那几行。"
      "排查问题时非常省事，注意过滤关键字要写对，写错会得到空输出。",
      forms={
          C: F(r"^show .+\|", "show ... | { include | exclude | begin | section } <关键字>"),
          R: F(r"^show .+\|", "show ... | { include | exclude | begin | section } <关键字>"),
      }),

    # ==================================================================
    # 第三批：IS-IS
    # ==================================================================

    D("启用 IS-IS 与 NET", "三层",
      "打开 IS-IS 进程并配置 NET。NET 由「区域号 + System ID + NSEL」组成，"
      "System ID 习惯上把 Loopback 地址补全成 12 位来写，便于排障时对号入座。",
      params=(("NET", "区域号 49.0001 是私有地址段；NSEL 固定写 00。"),
              ("System ID", "6 字节，通常写成 0000.0000.0001 这种形式。")),
      caution="同一台设备只能有一个 IS-IS 实例运行在 Level-2（含 Level-1-2）。"
              "官方建议 cost-style、is-level、network-entity 一起改时把 NET 放最后配。",
      forms={
          H: (F(r"^isis\s+\d+$", "isis <进程号>"),
              F(r"^network-entity\s+", "network-entity <NET>")),
          W: (F(r"^isis\s+\d+$", "isis <进程号>"),
              F(r"^network-entity\s+", "network-entity <NET>")),
          C: (F(r"^router isis$", "router isis [<标签>]"),
              F(r"^net\s+", "net <NET>")),
          R: (F(r"^router isis$", "router isis [<标签>]"),
              F(r"^net\s+", "net <NET>")),
      }),

    D("IS-IS 设备层级", "三层",
      "决定这台设备维护哪几张链路状态库：Level-1 只管本区域，"
      "Level-2 是骨干，Level-1-2 同时维护两张、可以跨区域。",
      params=(("层级", "纯 IP 网络里全部配 Level-2 最省事，扩展性也最好。"),),
      forms={
          H: F(r"^is-level\s+", "is-level { level-1 | level-1-2 | level-2 }",
               "缺省是 level-1-2。"),
          W: F(r"^is-level\s+", "is-level { level-1 | level-1-2 | level-2 }"),
          C: F(r"^is-type\s+", "is-type { level-1 | level-1-2 | level-2-only }",
               "思科多一个 only：写 level-2-only 而不是 level-2。"),
          R: F(r"^is-type\s+", "is-type { level-1 | level-1-2 | level-2-only }"),
      }),

    D("IS-IS 度量风格", "三层",
      "决定接口开销用哪种编码：narrow 是老标准的 6 位（最大 63），"
      "wide 是 32 位（最大 16777214），现代网络一律用 wide。",
      caution="全网必须统一，混用会导致路径计算异常。",
      forms={
          H: F(r"^cost-style\s+", "cost-style { narrow | wide | wide-compatible | compatible }"),
          W: F(r"^cost-style\s+", "cost-style { narrow | wide | wide-compatible | compatible }"),
          C: F(r"^metric-style\s+", "metric-style { narrow | wide | transition }"),
          R: F(r"^metric-style\s+", "metric-style { narrow | wide | transition }"),
      }),

    D("接口使能 IS-IS", "三层",
      "把接口加入 IS-IS 进程。注意跑 IS-IS 的必须是三层接口，"
      "交换机上通常是 VLAN 三层接口，或者切成三层的物理口。",
      caution="思科 / 锐捷的物理口要先 no switchport 才能配 IP 和跑路由。",
      forms={
          H: F(r"^isis enable\s+", "isis enable [<进程号>]"),
          W: F(r"^isis enable\s+", "isis enable [<进程号>]"),
          C: F(r"^ip router isis", "ip router isis [<标签>]"),
          R: F(r"^ip router isis", "ip router isis [<标签>]"),
      }),

    D("IS-IS 接口邻接层级", "三层",
      "限制这个接口只建立某一层的邻接关系。只跑单层时配上可以少收一半的 PDU。",
      forms={
          H: F(r"^isis circuit-level\s+", "isis circuit-level { level-1 | level-1-2 | level-2 }"),
          W: F(r"^isis circuit-level\s+", "isis circuit-level { level-1 | level-1-2 | level-2 }"),
          C: F(r"^isis circuit-type\s+", "isis circuit-type { level-1 | level-1-2 | level-2-only }"),
          R: F(r"^isis circuit-type\s+", "isis circuit-type { level-1 | level-1-2 | level-2-only }"),
      }),

    D("IS-IS 接口验证", "三层",
      "对 Hello 报文做验证，防止有人把设备误接进来形成非法邻接。",
      caution="H3C / 华为直接在接口下写密钥；思科 / 锐捷要先建钥匙串再到接口引用。"
              "两端密钥必须一致，不一致邻接建不起来。",
      forms={
          H: F(r"^isis authentication-mode\s+", "isis authentication-mode md5 { cipher | plain } <密钥>"),
          W: F(r"^isis authentication-mode\s+", "isis authentication-mode md5 { cipher | plain } <密钥>",
               "华为还支持 hmac-sha256：isis authentication-mode hmac-sha256 key-id 1 cipher <密钥>。"),
          C: (F(r"^key chain\s+", "key chain <名称> → key <编号> → key-string <密钥>"),
              F(r"^isis authentication\s+", "isis authentication mode md5 / isis authentication key-chain <名称>")),
          R: (F(r"^key chain\s+", "key chain <名称> → key <编号> → key-string <密钥>"),
              F(r"^isis authentication\s+", "isis authentication mode md5 / isis authentication key-chain <名称>")),
      }),

    D("查看 IS-IS 邻接", "三层",
      "看 IS-IS 邻接关系是不是 Up、对端是谁、跑在哪一层。",
      forms={
          H: F(r"^display isis", "display isis { peer | route | lsdb }"),
          W: F(r"^display isis", "display isis { peer | route | lsdb }"),
          C: F(r"^show isis neighbors$", "show isis neighbors"),
          R: F(r"^show isis neighbors$", "show isis neighbors"),
      }),

    # ==================================================================
    # 第三批：BGP
    # ==================================================================

    D("启用 BGP 并指定 Router ID", "三层",
      "打开 BGP 进程（后面的数字就是本机 AS 号），并手工指定 Router ID。",
      caution="Router ID 冲突会让邻居反复震荡，建议指定成 Loopback 地址，别依赖自动选举。",
      forms={
          H: (F(r"^bgp\s+\d+$", "bgp <本机AS号>"),
              F(r"^router-id\s+", "router-id <IP>")),
          W: (F(r"^bgp\s+\d+$", "bgp <本机AS号>"),
              F(r"^router-id\s+", "router-id <IP>")),
          C: (F(r"^router bgp\s+", "router bgp <本机AS号>"),
              F(r"^bgp router-id\s+", "bgp router-id <IP>")),
          R: (F(r"^router bgp\s+", "router bgp <本机AS号>"),
              F(r"^bgp router-id\s+", "bgp router-id <IP>")),
      }),

    D("创建 BGP 邻居", "三层",
      "指定对端地址和对端的 AS 号。对端 AS 与本机相同就是 IBGP，不同就是 EBGP。",
      params=(("邻居地址", "EBGP 一般是直连口地址；IBGP 建议用 Loopback。"),
              ("对端 AS", "写错会一直卡在 Connect / Active。")),
      forms={
          H: F(r"^peer\s+\S+\s+as-number\s+", "peer <邻居IP> as-number <对端AS>"),
          W: F(r"^peer\s+\S+\s+as-number\s+", "peer <邻居IP> as-number <对端AS>"),
          C: F(r"^neighbor\s+\S+\s+remote-as\s+", "neighbor <邻居IP> remote-as <对端AS>"),
          R: F(r"^neighbor\s+\S+\s+remote-as\s+", "neighbor <邻居IP> remote-as <对端AS>"),
      }),

    D("在地址族里激活邻居", "三层",
      "H3C / 华为光创建邻居还不够，必须进对应地址族再 peer enable 一次，"
      "邻居才会真正交换路由。",
      caution="这是最常见的「配了却不通」原因之一：邻居显示 Idle 或者根本没起来。",
      forms={
          H: (F(r"^address-family ipv4 unicast$", "address-family ipv4 unicast",
                "进入 IPv4 单播地址族视图。"),
              F(r"^peer\s+\S+\s+enable$", "peer <邻居IP> enable")),
          W: (F(r"^ipv4-family unicast$", "ipv4-family unicast",
                "华为的地址族关键字是 ipv4-family，不是 address-family。"),
              F(r"^peer\s+\S+\s+enable$", "peer <邻居IP> enable")),
          C: (F(r"^no bgp default ipv4-unicast$", "no bgp default ipv4-unicast",
                "关闭默认的 IPv4 单播自动激活，PE 上常用，避免公网路由被无谓发出。"),
              F(r"^neighbor\s+\S+\s+activate$", "neighbor <邻居IP> activate")),
          R: (F(r"^no bgp default ipv4-unicast$", "no bgp default ipv4-unicast",
                "关闭默认的 IPv4 单播自动激活。"),
              F(r"^neighbor\s+\S+\s+activate$", "neighbor <邻居IP> activate")),
      }),

    D("BGP 邻居描述与验证", "三层",
      "给邻居加一句描述便于识别，或用 TCP MD5 给会话加一层验证。",
      caution="MD5 密钥两端必须完全一致，不一致会一直卡在 Connect 状态。",
      forms={
          H: (F(r"^peer\s+\S+\s+description\s+", "peer <邻居IP> description <描述>"),
              F(r"^peer\s+\S+\s+password\s+", "peer <邻居IP> password { cipher | simple } <密钥>")),
          W: (F(r"^peer\s+\S+\s+description\s+", "peer <邻居IP> description <描述>"),
              F(r"^peer\s+\S+\s+password\s+", "peer <邻居IP> password { cipher | simple } <密钥>")),
          C: (F(r"^neighbor\s+\S+\s+description\s+", "neighbor <邻居IP> description <描述>"),
              F(r"^neighbor\s+\S+\s+password\s+", "neighbor <邻居IP> password <密钥>"),
              F(r"^neighbor\s+\S+\s+timers\s+", "neighbor <邻居IP> timers <Keepalive> <Hold>")),
          R: (F(r"^neighbor\s+\S+\s+description\s+", "neighbor <邻居IP> description <描述>"),
              F(r"^neighbor\s+\S+\s+password\s+", "neighbor <邻居IP> password <密钥>"),
              F(r"^neighbor\s+\S+\s+timers\s+", "neighbor <邻居IP> timers <Keepalive> <Hold>")),
      }),

    D("查看 BGP 邻居状态", "三层",
      "看 BGP 邻居有没有起来。State/PfxRcd 那一列出现数字（收到的路由条数）"
      "就说明会话已经 Established。",
      caution="卡在 Connect 多半是 TCP 连不通或 MD5 不一致；"
              "卡在 Active 通常是邻居地址不可达。",
      forms={
          H: F(r"^display bgp", "display bgp { peer | routing-table } [ipv4]"),
          W: F(r"^display bgp", "display bgp { peer | routing-table }"),
          C: F(r"^show (ip )?bgp", "show ip bgp summary / show ip bgp"),
          R: F(r"^show (ip )?bgp", "show bgp summary / show bgp ipv4 unicast"),
      }),

    # ==================================================================
    # 第三批：MPLS L3VPN
    # ==================================================================

    D("配置环回口作为 LSR ID", "三层",
      "MPLS 需要一个稳定的标识，用 Loopback 地址最合适——只要设备不重启它就不会 down，"
      "LDP 会话也就不会断。",
      forms={
          H: F(r"^interface Loopback", "interface Loopback<编号>"),
          W: F(r"^interface LoopBack", "interface LoopBack<编号>",
               "华为的环回口写法是大写 B 的 LoopBack0。"),
          R: F(r"^interface Loopback", "interface Loopback <编号>",
               "锐捷的接口名与编号之间有空格。"),
      }),

    D("开启 MPLS 与 LDP", "三层",
      "在公网侧接口上打开 MPLS 转发和 LDP 标签分发。"
      "LDP 负责和邻居协商标签，标签是 MPLS 转发的基础。",
      params=(("LSR ID", "MPLS 域内的唯一标识，通常就是 Loopback 地址。"),
              ("LDP Router ID", "不能用 0.0.0.0，必须是 32 位地址。")),
      caution="前提是公网侧 IGP 已经跑通，且两端的 Loopback 都宣告进了 IGP，"
              "否则 LDP 会话建不起来。接 CE 的接口不要开 MPLS。",
      forms={
          H: (F(r"^mpls lsr-id\s+", "mpls lsr-id <IP>", "H3C 没有单独的全局 mpls 命令。"),
              F(r"^mpls ldp$", "mpls ldp", "全局开启 LDP。"),
              F(r"^mpls enable$", "mpls enable", "接口下开启 MPLS 转发。"),
              F(r"^mpls ldp enable$", "mpls ldp enable", "接口下开启 LDP。")),
          W: (F(r"^mpls lsr-id\s+", "mpls lsr-id <IP>"),
              F(r"^mpls$", "mpls", "全局开启 MPLS 转发。"),
              F(r"^mpls ldp$", "mpls ldp", "全局开启 LDP；接口下也是 mpls 和 mpls ldp 两条。")),
          C: (F(r"^mpls label protocol ldp$", "mpls label protocol ldp"),
              F(r"^mpls ldp router-id\s+", "mpls ldp router-id <接口> [force]",
                "force 让新的 Router ID 立即生效，不用等会话超时。"),
              F(r"^mpls ip$", "mpls ip",
                "全局和接口下都是这条命令，作用都是开启 MPLS 转发。")),
          R: (F(r"^mpls ip$", "mpls ip", "锐捷用 mpls ip 全局开启 MPLS 转发。"),
              F(r"^mpls router ldp$", "mpls router ldp", "进入 LDP 配置模式。"),
              F(r"^ldp router-id\s+", "ldp router-id interface <接口> [force]",
                "锐捷没有 mpls lsr-id，用这条指定 LDP Router ID。"),
              F(r"^label-switch$", "label-switch",
                "接口下开启标签交换。部分版本写作 label-switching，下发前用 ? 确认。")),
      }),

    D("创建 VPN 实例（VRF）", "三层",
      "在一台物理设备上虚拟出多张独立的路由表，不同 VPN 的同网段地址就不会冲突。",
      params=(("RD", "路由标识，给路由打上「属于哪个 VPN」的标记，两台 PE 上不能重复。"),
              ("RT", "路由目标，决定路由在哪些 VPN 之间导入导出。")),
      caution="本机的 RT 导入值要对上对端 PE 的导出值，配反了就是不通——"
              "这是 MPLS VPN 不通最常见的原因。",
      forms={
          H: (F(r"^ip vpn-instance\s+", "ip vpn-instance <实例名>"),
              F(r"^route-distinguisher\s+", "route-distinguisher <RD>"),
              F(r"^vpn-target\s+", "vpn-target <RT> { import-extcommunity | export-extcommunity }",
                "H3C 的 RD / RT 直接配在实例视图下。")),
          W: (F(r"^ip vpn-instance\s+", "ip vpn-instance <实例名>"),
              F(r"^ipv4-family$", "ipv4-family",
                "华为要先进 ipv4-family 子视图，RD / RT 配在这里面。"),
              F(r"^route-distinguisher\s+", "route-distinguisher <RD>"),
              F(r"^vpn-target\s+", "vpn-target <RT> { export-extcommunity | import-extcommunity }")),
          C: (F(r"^ip vrf\s+", "ip vrf <实例名>",
                "老写法；较新的 IOS-XE 也可以写 vrf definition，两者不能混用。"),
              F(r"^rd\s+", "rd <RD>"),
              F(r"^route-target\s+", "route-target { export | import } <RT>")),
          R: (F(r"^ip vrf\s+", "ip vrf <实例名>"),
              F(r"^rd\s+", "rd <RD>"),
              F(r"^route-target\s+", "route-target { export | import } <RT>")),
      }),

    D("把接口划入 VPN 实例", "三层",
      "把接 CE 的接口绑定到 VPN 实例，这个口的地址就属于那张独立路由表了。",
      caution="绑定操作会清掉接口上原有的 IP 配置，所以必须先绑实例、再配地址，"
              "顺序反了地址就没了。这是配 MPLS VPN 最容易踩的坑。",
      forms={
          H: F(r"^ip binding vpn-instance\s+", "ip binding vpn-instance <实例名>"),
          W: F(r"^ip binding vpn-instance\s+", "ip binding vpn-instance <实例名>"),
          C: F(r"^ip vrf forwarding\s+", "ip vrf forwarding <实例名>"),
          R: F(r"^ip vrf forwarding\s+", "ip vrf forwarding <实例名>"),
      }),

    D("MP-BGP 传递 VPN 路由", "三层",
      "PE 之间用 VPNv4 地址族交换 VPN 路由，PE 与 CE 之间用普通 IPv4 邻居交换私网路由。",
      params=(("VPNv4 地址族", "PE 之间传 VPN 路由用，路由带 RD 和 RT 扩展团体。"),
              ("VPN 实例地址族", "PE 与 CE 之间的私网邻居配在这里。")),
      caution="send-community extended 必须配，VPNv4 靠扩展团体携带 RT；"
              "next-hop-local 让 PE 把下一跳改成本机，避免对端迭代不出隧道。",
      forms={
          H: (F(r"^address-family vpnv4$", "address-family vpnv4"),
              F(r"^ip vpn-instance\s+", "ip vpn-instance <实例名>（BGP 视图下进入该 VPN 的地址族）"),
              F(r"^peer\s+\S+\s+next-hop-local$", "peer <邻居IP> next-hop-local"),
              F(r"^peer\s+\S+\s+connect-interface\s+", "peer <邻居IP> connect-interface <接口>")),
          W: (F(r"^ipv4-family vpnv4$", "ipv4-family vpnv4"),
              F(r"^policy vpn-target$", "policy vpn-target",
                "按 RT 过滤收到的 VPN 路由，这是默认行为，写出来便于理解。"),
              F(r"^ipv4-family vpn-instance\s+", "ipv4-family vpn-instance <实例名>"),
              F(r"^peer\s+\S+\s+connect-interface\s+", "peer <邻居IP> connect-interface <接口>")),
          C: (F(r"^address-family vpnv4$", "address-family vpnv4"),
              F(r"^address-family ipv4 vrf\s+", "address-family ipv4 vrf <实例名>"),
              F(r"^neighbor\s+\S+\s+send-community extended$", "neighbor <邻居IP> send-community extended"),
              F(r"^neighbor\s+\S+\s+update-source\s+", "neighbor <邻居IP> update-source <接口>")),
          R: (F(r"^address-family vpnv4 unicast$", "address-family vpnv4 unicast",
                "锐捷比思科多一个 unicast。"),
              F(r"^address-family ipv4 vrf\s+", "address-family ipv4 vrf <实例名>"),
              F(r"^neighbor\s+\S+\s+send-community extended$", "neighbor <邻居IP> send-community extended"),
              F(r"^neighbor\s+\S+\s+update-source\s+", "neighbor <邻居IP> update-source <接口>")),
      }),

    D("MPLS VPN 分段核对", "三层",
      "MPLS VPN 出问题要分段查，从下往上：LDP 邻居 → 标签 → VPN 实例 → MP-BGP → VPN 路由。",
      caution="哪一段断了就在哪一段停下排查，别一次看五张表，容易越看越乱。",
      forms={
          H: F(r"^display (mpls|ip vpn-instance|bgp vpnv4)",
               "display { mpls ldp peer | mpls ldp lsp | ip vpn-instance | bgp vpnv4 ... }"),
          W: F(r"^display (mpls|ip vpn-instance|bgp vpnv4)",
               "display { mpls ldp peer | mpls lsp | ip vpn-instance | bgp vpnv4 all peer | ... }"),
          C: F(r"^show (mpls|ip vrf|ip route vrf|ip bgp vpnv4)",
               "show { mpls ldp neighbor | ip vrf | ip route vrf <实例> | ip bgp vpnv4 all summary }"),
          R: F(r"^show (mpls|ip vrf|ip route vrf|bgp vpnv4)",
               "show { mpls ldp neighbor | ip vrf | ip route vrf <实例> | bgp vpnv4 unicast summary }"),
      }),

    # ------------------------------------------------------------------
    # 补充：路由引入、地址族、接口与三层开关
    # ------------------------------------------------------------------

    D("引入路由（把别的协议的路由发进来）", "三层",
      "把直连、静态或其它协议学到的路由引入当前协议，让它们也能被发布出去。",
      params=(("direct / connected", "引入直连网段，最常用。"),
              ("static", "引入静态路由。")),
      caution="引入时最好配路由策略过滤，否则容易把不该发布的路由也带出去，"
              "严重时会造成路由环路。",
      forms={
          H: (F(r"^import-route\s+", "import-route { direct | static | ospf <进程> }"),
              F(r"^  import-route\s+", "import-route { direct | static | ospf <进程> }")),
          W: (F(r"^import-route\s+", "import-route { direct | static | ospf <进程> }"),
              F(r"^  import-route\s+", "import-route { direct | static | ospf <进程> }")),
          C: F(r"^ ?redistribute\s+", "redistribute { connected | static | ospf <进程> }"),
          R: F(r"^ ?redistribute\s+", "redistribute { connected | static | ospf <进程> }"),
      }),

    D("进入 / 退出地址族视图", "三层",
      "BGP 和 IS-IS 都按地址族组织配置：先进某个地址族视图，配完再退出来。"
      "H3C / 华为的 IS-IS 要在 address-family ipv4 里写 import-route，"
      "写在主视图里会报 Unrecognized command。",
      forms={
          H: F(r"^address-family (ipv4|ipv6|vpnv4|vpnv6)( unicast)?$",
               "address-family { ipv4 [ unicast ] | ipv6 [ unicast ] | vpnv4 | vpnv6 }"),
          W: (F(r"^ipv4-family( unicast)?$", "ipv4-family [ unicast ]",
                "BGP 的地址族视图。"),
              F(r"^address-family ipv4$", "address-family ipv4",
                "IS-IS 打 import-route 时用的是这个地址族。")),
          C: F(r"^exit-address-family$", "exit-address-family", "从地址族视图退出来。"),
          R: F(r"^exit-address-family$", "exit-address-family"),
      }),

    D("三层接口命名（VLAN 接口）", "三层",
      "交换机上跑路由通常用的是 VLAN 三层接口：先建 VLAN，再给 VLAN 接口配地址。"
      "四家对这个接口的叫法不一样，填参数时注意区分。",
      forms={
          H: F(r"^interface Vlan-interface\s+", "interface Vlan-interface <VLAN号>"),
          W: F(r"^interface Vlanif", "interface Vlanif<VLAN号>",
               "华为的 VLAN 三层接口写作 Vlanif10，中间没有空格。"),
          C: F(r"^interface Vlan\s?\d+$", "interface Vlan<VLAN号>"),
          R: F(r"^interface Vlan\s+\d+$", "interface Vlan <VLAN号>",
               "锐捷的接口名与编号之间有空格。"),
      }),

    D("开启 CEF（思科 MPLS 的前置条件）", "三层",
      "思科跑 MPLS 必须先打开 Cisco Express Forwarding，"
      "LDP 的标签转发信息要靠它下发到硬件。",
      caution="没开 CEF 时 MPLS 相关配置能敲进去但不生效，"
              "表现为「配置都对、标签就是不下发」。",
      forms={
          C: F(r"^ip cef$", "ip cef"),
      }),

    D("BGP 邻居变化日志", "三层",
      "邻居建立或断开时打一条日志，排障时能看出它是什么时候掉的。",
      forms={
          C: F(r"^bgp log-neighbor-changes$", "bgp log-neighbor-changes"),
          R: F(r"^bgp log-neighbor-changes$", "bgp log-neighbor-changes"),
      }),

    D("network 宣告网段", "三层",
      "network 这条命令在不同视图下含义完全不同："
      "在 DHCP 地址池视图下是「声明这个地址池的网段」，"
      "在 BGP 视图下是「把这个网段对外宣告出去」。"
      "看上下文判断，别混。",
      caution="BGP 里宣告的网段必须在本机路由表里存在，否则宣告不出去。",
      forms={
          H: F(r"^network\s+\S+\s+\d+\.\d+\.\d+\.\d+$", "network <网段> <点分掩码>"),
          W: F(r"^network\s+\S+\s+\d+\.\d+\.\d+\.\d+$", "network <网段> <点分掩码>"),
          C: F(r"^network\s+\S+\s+mask\s+\d+\.\d+\.\d+\.\d+$", "network <网段> mask <点分掩码>"),
          R: F(r"^network\s+\S+\s+mask\s+\d+\.\d+\.\d+\.\d+$", "network <网段> mask <点分掩码>"),
      }),
)


# ==========================================================================
# 索引与匹配
# ==========================================================================

_COMPILED: dict = {}


def _compile() -> None:
    for doc in CATALOG:
        for vk in doc.forms:
            for form in doc.forms_of(vk):
                _COMPILED.setdefault(vk, []).append((re.compile(form.pattern), doc))
    for vk, items in _COMPILED.items():
        # 更具体的模式先尝试，避免被笼统模式抢走
        items.sort(key=lambda pair: -len(pair[0].pattern))


_compile()


def vendors_for(doc: Doc) -> tuple:
    return tuple(v for v in VENDOR_ORDER if v in doc.forms)


def docs_for(vendor_key: str) -> tuple:
    """该厂商适用的全部概念。"""
    return tuple(d for d in CATALOG if vendor_key in d.forms)


def match(vendor_key: str, line: str):
    """把一行命令匹配到概念，匹配不到返回 None。"""
    text = (line or "").strip()
    if not text:
        return None
    for rx, doc in _COMPILED.get(vendor_key, ()):
        if rx.match(text):
            return doc
    return None


def explain_lines(vendor_key: str, lines) -> list:
    """返回 [(命令行, 概念或 None), ...]，保持原顺序。"""
    return [(ln, match(vendor_key, ln)) for ln in lines]


# ==========================================================================
# 渲染
# ==========================================================================

def _disp_width(text: str) -> int:
    """按显示宽度计宽：中日韩字符算 2 列，其余算 1 列。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """左侧文本按显示宽度右侧补空格，让四厂商的语法列对齐。"""
    return text + " " * max(2, width - _disp_width(text))


def render_doc(doc: Doc, vendor_key: str, compare: bool = True) -> list:
    """把一条概念渲染成多行文本（供 Text 控件逐行插入）。"""
    out = [f"【{doc.title}】  {doc.group}"]
    forms = doc.forms_of(vendor_key)
    if forms:
        out.append(f"  语法    {forms[0].syntax}")
    out.append(f"  作用    {doc.desc}")
    if doc.params:
        out.append("  参数")
        for item in doc.params:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                out.append(f"          {item[0]}：{item[1]}")
            else:  # 容错：写成裸字符串时不至于让界面崩掉
                out.append(f"          {item}")
    if doc.caution:
        out.append(f"  注意    {doc.caution}")
    notes = doc.notes_of(vendor_key)
    if notes:
        out.append(f"  版本    {'；'.join(notes)}")
    if compare:
        others = [(v, doc.syntax_of(v)) for v in vendors_for(doc) if v != vendor_key]
        if others:
            names = {H: "H3C", W: "华为", C: "思科", R: "锐捷"}
            out.append("  ── 同一件事，别家怎么写 ──")
            for v, syn in others:
                out.append(f"      {_pad(names[v], 6)}{syn}")
    return out


def render_explanation(vendor_key: str, result) -> list:
    """针对一次生成结果，按「首次出现」去重后逐条详解。"""
    names = {H: "H3C", W: "华为", C: "思科", R: "锐捷"}
    out = [f"━━ {names[vendor_key]} 命令详解 ━━",
           f"   场景：{result.scenario_name}",
           "   下面按概念首次出现的顺序解释，重复出现的同类命令不再赘述。",
           ""]

    seen: list = []
    for block in result.blocks:
        for line in block.lines:
            doc = match(vendor_key, line)
            if doc is not None and doc not in seen:
                seen.append(doc)

    if not seen:
        out.append("本次生成的内容以视图切换命令为主，没有可解析的配置命令。")
        return out

    for doc in seen:
        out.extend(render_doc(doc, vendor_key))
        out.append("")
    return out


def render_manual(vendor_key: str) -> list:
    """该厂商的完整命令手册，按分组排列。"""
    names = {H: "H3C", W: "华为", C: "思科", R: "锐捷"}
    docs = docs_for(vendor_key)
    out = [f"━━ {names[vendor_key]} 命令手册（共 {len(docs)} 条概念）━━", ""]
    for group in GROUP_ORDER:
        items = [d for d in docs if d.group == group]
        if not items:
            continue
        out.append(f"▌{group}")
        for d in items:
            out.append(f"  · {d.title}")
            out.append(f"        {d.syntax_of(vendor_key)}")
        out.append("")
    return out


def render_compare() -> list:
    """四厂商命令对照表：同一件事各家怎么写。"""
    names = ((H, "H3C"), (W, "华为"), (C, "思科"), (R, "锐捷"))
    out = ["━━ 四厂商命令对照 ━━",
           "   只列出两家以上厂商都有的概念；单厂商专有命令见各自的手册。",
           ""]
    for group in GROUP_ORDER:
        items = [d for d in CATALOG if d.group == group and len(d.forms) >= 2]
        if not items:
            continue
        out.append(f"▌{group}")
        for d in items:
            out.append(f"  · {d.title}")
            for vk, label in names:
                if vk in d.forms:
                    out.append(f"      {_pad(label, 6)}{d.syntax_of(vk)}")
            out.append("")
    return out


def coverage(vendor_key: str, result) -> tuple:
    """统计一次生成结果里有多少行能被详解覆盖，返回 (已覆盖, 总行数, 未识别行列表)。"""
    total = 0
    hit = 0
    miss: list = []
    for block in result.blocks:
        for line in block.lines:
            if not line.strip():
                continue
            total += 1
            if match(vendor_key, line) is not None:
                hit += 1
            else:
                miss.append(line)
    return hit, total, miss
