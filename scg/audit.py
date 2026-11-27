"""现网配置体检。

把设备上 ``display current-configuration`` / ``show running-config`` 的输出整段
粘进来，这里跑一遍规则集，给出「风险 / 通过 / 建议」三段式报告，附健康分和
可直接粘贴的修复命令。

为什么要有这个：生成配置解决的是"怎么写"，而真实交付场景里更常被问的是
"我手上这台设备有没有问题"。等保测评、项目验收、接手别人的网络，
都要先出一份现状评估，以前只能靠人肉逐屏看。

三条设计原则：

1. **不连设备，只吃文本**。工具始终离线的定位不能破。
2. **宁可说"没查到"，不可误报**。用户可能只粘了一段配置，这时"未配置 NTP"
   很可能是假阳性 —— 所以先判断配置完整性，不完整时把结论降级为建议。
3. **每条风险都要能修**。只给问题不给修复命令的报告，网工看完还得再查一遍。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """一条体检结论。"""

    level: str          # high / mid / low / pass / info
    title: str
    evidence: str = ""
    fix: str = ""
    detail: str = ""
    weight: int = 0


@dataclass
class Report:
    vendor_key: str = ""
    vendor_name: str = ""
    vendor_evidence: str = ""
    score: int = 100
    complete: bool = True
    stats: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)

    def by_level(self, level: str) -> list:
        return [f for f in self.findings if f.level == level]

    @property
    def high(self) -> list:
        return self.by_level("high")

    @property
    def mid(self) -> list:
        return self.by_level("mid")

    @property
    def low(self) -> list:
        return self.by_level("low")

    @property
    def passed(self) -> list:
        return self.by_level("pass")


# --------------------------------------------------------------------------
# 厂商识别
# --------------------------------------------------------------------------

#: 特征串 -> 权重。分数最高的厂商胜出，平局时按出现顺序取前者。
_SIGNS = {
    "h3c": (
        ("interface Vlan-interface", 6),
        ("Comware", 6),
        ("local-user", 2),
        ("system-view", 2),
        ("sysname", 1),
    ),
    "huawei": (
        ("interface Vlanif", 6),
        ("user-interface", 6),
        ("VRP", 6),
        ("authentication-scheme", 3),
        ("sysname", 1),
        ("aaa", 1),
    ),
    "cisco": (
        ("Port-channel", 5),
        ("channel-group", 4),
        ("crypto key", 4),
        ("service timestamps", 4),
        ("ip cef", 3),
        ("hostname", 1),
        ("interface Vlan", 2),
        ("switchport", 3),
    ),
    "ruijie": (
        ("enable service", 6),
        ("AggregatePort", 6),
        ("vsl-port", 6),
        ("port-group", 4),
        ("switch 1", 3),
        ("hostname", 1),
        ("switchport", 3),
    ),
}

_VENDOR_LABEL = {
    "h3c": "H3C 新华三 Comware",
    "huawei": "华为 VRP",
    "cisco": "思科 IOS",
    "ruijie": "锐捷 RGOS",
}


def detect_vendor(text: str) -> tuple:
    """按特征串打分识别厂商，返回 ``(key, 依据说明)``。"""
    scores: dict = {}
    hits: dict = {}
    for key, signs in _SIGNS.items():
        total = 0
        found = []
        for token, weight in signs:
            if token.lower() in text.lower():
                total += weight
                found.append(token)
        scores[key] = total
        hits[key] = found

    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return "", "未识别（配置里没有可辨认的厂商特征）"
    evidence = "、".join(hits[best][:4])
    return best, f"依据：{evidence}"


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


@dataclass
class _Ctx:
    """体检要用的配置视图。"""

    text: str
    vendor: str
    lines: list = field(default_factory=list)
    globals: list = field(default_factory=list)   # [(命令行, [子命令])]
    ifaces: dict = field(default_factory=dict)    # 接口名 -> [子命令]

    @property
    def lower(self) -> str:
        return self.text.lower()

    def has(self, *tokens) -> bool:
        """全文包含任一特征即算命中（大小写不敏感）。"""
        low = self.lower
        return any(t.lower() in low for t in tokens)

    def count(self, pattern: str) -> int:
        return len(re.findall(pattern, self.text, re.M | re.I))

    def enabled(self, pattern: str) -> bool:
        """某条命令是否处于「生效」状态。

        必须排除 ``undo`` / ``no`` 形式 —— 安全基线里关掉 Telnet 的写法正是
        ``undo telnet server enable``，如果按子串匹配，会把"已经关掉"判成
        "还开着"，这是体检工具最容易冤枉人的地方。
        """
        rx = re.compile(pattern, re.I)
        for line in self.lines:
            text = line.strip()
            if not rx.match(text):
                continue
            low = text.lower()
            if low.startswith("undo ") or low.startswith("no "):
                continue
            return True
        return False

    def iface_items(self):
        for name, body in self.ifaces.items():
            yield name, body


def _parse(text: str, vendor: str) -> _Ctx:
    """逐行解析，跟踪「当前在哪个接口里」。

    不按 ``#`` / ``!`` 切块，是因为用户粘进来的东西未必是原样导出的配置 ——
    可能是文档里摘的一段、可能少了分隔符。逐行判断更耐操：见到 ``interface``
    就进接口上下文，见到分隔符或下一个视图就退出来。
    """
    ctx = _Ctx(text=text, vendor=vendor)
    ctx.lines = text.splitlines()

    current = None
    for raw in ctx.lines:
        head = raw.strip()
        if not head:
            continue
        if head in ("#", "!"):
            current = None
            continue
        if head.startswith("!") and len(head) > 1:
            continue                      # 思科的行内注释
        if head.startswith("#"):
            continue

        match = re.match(r"^interface\s+(.+?)\s*$", head, re.I)
        if match:
            current = match.group(1)
            ctx.ifaces.setdefault(current, [])
            continue

        if current is not None:
            ctx.ifaces[current].append(head)
        else:
            ctx.globals.append((head, []))

    return ctx


# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------


def _f(level, title, evidence="", fix="", detail="", weight=0) -> Finding:
    return Finding(level, title, evidence, fix, detail, weight)


def _rule_remote(ctx: _Ctx) -> list:
    """远程管理面：Telnet / HTTP / SSH。"""
    out: list = []

    telnet_on = ctx.enabled(r"telnet server enable") or ctx.enabled(
        r"transport input .*telnet")
    ssh_on = (ctx.enabled(r"ssh server enable") or ctx.enabled(r"stelnet server enable")
              or ctx.enabled(r"ip ssh version") or ctx.enabled(r"enable service ssh-server")
              or ctx.enabled(r"transport input .*ssh")
              or ctx.has("ip ssh version"))

    if telnet_on:
        if ctx.vendor in ("h3c", "huawei"):
            fix = "undo telnet server enable"
        else:
            fix = "line vty 0 4\n transport input ssh"
        out.append(_f("high", "设备开着 Telnet 服务",
                      "telnet server enable / transport input telnet", fix,
                      "Telnet 全程明文，口令和配置内容都能被抓包直接看到。"
                      "只要链路上有人抓流量，管理员账号就等于泄露了。", 15))
    if not ssh_on:
        out.append(_f("mid", "没有发现启用 SSH 的配置",
                      "", "ssh server enable（H3C/华为）\n"
                          "crypto key generate rsa modulus 2048 + ip ssh version 2（思科/锐捷）",
                      "没有 SSH 就只能用 Telnet 或 Console 管理，前者明文、"
                      "后者要跑机房。远程管理建议统一收敛到 SSH v2。", 10))
    elif not telnet_on:
        out.append(_f("pass", "远程管理走 SSH", "已启用 SSH，未发现 Telnet 开启"))

    if ctx.enabled(r"ip http (secure-)?server"):
        out.append(_f("mid", "开启了 HTTP/HTTPS 管理界面",
                      "ip http server / ip http secure-server",
                      "no ip http server\nno ip http secure-server",
                      "Web 管理界面的攻击面比 CLI 大得多，历史上出过不少越权与"
                      "命令注入问题。不开就不开，需要时再临时开。", 8))

    return out


def _rule_snmp(ctx: _Ctx) -> list:
    out: list = []

    communities = re.findall(r"snmp-server community (\S+)", ctx.text, re.I)
    communities += re.findall(r"snmp-agent community (?:read|write) "
                              r"(?:simple|cipher)?\s*(\S+)", ctx.text, re.I)
    communities = [c.strip("'\"") for c in communities]

    weak = {"public", "private", "read", "write", "admin", "123456", "snmp"}
    hit_weak = [c for c in communities if c.lower() in weak]

    if hit_weak:
        out.append(_f("high", "SNMP 团体字用了默认值或弱口令",
                      "、".join(sorted(set(hit_weak))),
                      "snmp-agent community read cipher <新团体字>\n"
                      "snmp-server community <新团体字> RO",
                      "public / private 是扫描器的第一批字典，命中就等于把接口"
                      "流量、路由表、ARP 表全交出去。", 12))
    elif communities:
        out.append(_f("pass", "SNMP 团体字非默认值",
                      "社团名已自定义"))

    if ctx.enabled(r"snmp-agent sys-info version v2c") or ctx.enabled(
            r"snmp-server host .* version 2c"):
        out.append(_f("mid", "SNMP 仍在用 v2c",
                      "snmp-agent sys-info version v2c",
                      "snmp-agent sys-info version v3\n"
                      "snmp-server group <组> v3 priv",
                      "v2c 的团体字是明文传输的，等同于口令裸奔。"
                      "如果对端网管支持，建议切到 v3 并启用认证加密。", 6))

    return out


def _rule_password(ctx: _Ctx) -> list:
    out: list = []

    plain = re.findall(r"password simple (\S+)", ctx.text, re.I)
    plain += re.findall(r"password 0 (\S+)", ctx.text, re.I)
    plain += re.findall(r"^(\s*password\s+\S+)$", ctx.text, re.M)

    if plain:
        out.append(_f("high", "存在明文保存的口令",
                      f"共 {len(plain)} 处，例如：{plain[0][:40]}",
                      "password cipher <新口令>（H3C/华为）\n"
                      "username <用户> secret <新口令>（思科/锐捷）",
                      "明文口令会随配置文件一起被备份、被导出、被截图。"
                      "改用 cipher / secret 之后，配置里只剩不可逆摘要。", 12))
    elif ctx.has("password cipher", "password irreversible-cipher",
                 "secret 5", "username") and not plain:
        out.append(_f("pass", "口令未以明文保存"))

    if re.search(r"local-user \S+ class manage", ctx.text, re.I) and \
            not ctx.has("authorization-attribute user-role"):
        out.append(_f("low", "本地用户没有限定角色",
                      "local-user ... class manage 缺少 user-role",
                      "authorization-attribute user-role network-admin"
                      "（按需选择 network-operator）",
                      "不给 user-role 时角色依赖设备默认值，不同版本默认不一样，"
                      "容易出现「换台设备权限就变了」。", 3))

    return out


def _rule_ntp_log(ctx: _Ctx) -> list:
    out: list = []

    has_ntp = ctx.has("ntp-service unicast-server", "ntp server", "ntp-service enable")
    if has_ntp:
        out.append(_f("pass", "已配置 NTP 时间同步"))
    else:
        out.append(_f("mid", "没有配置 NTP 时间同步",
                      "", "ntp-service enable + ntp-service unicast-server <服务器>\n"
                          "ntp server <服务器>",
                      "设备时间不准，日志的时间戳就对不上，出故障时没法按时间线"
                      "还原过程；证书和 802.1X 认证也会因时间偏差失败。", 6))

    has_log = ctx.has("info-center loghost", "logging host", "logging server")
    if has_log:
        out.append(_f("pass", "日志已外送到日志服务器"))
    else:
        out.append(_f("mid", "日志只留在本地缓冲区",
                      "", "info-center loghost <服务器>\nlogging host <服务器>",
                      "设备重启或崩溃后本地日志一起消失。安全事件、配置变更、"
                      "接口震荡这些证据都在日志里，没有外送等于没有留痕。", 6))

    return out


def _rule_vlan1(ctx: _Ctx) -> list:
    out: list = []

    # 必须锚定行首行尾：否则 logging source-interface Vlan1 这种全局命令
    # 也会被当成「给 VLAN 1 配了三层接口」，冤枉一整批配置。
    vlan1_ip = re.search(
        r"^\s*interface\s+(?:Vlan-interface|Vlanif|Vlan)\s*1\s*$",
        ctx.text, re.M | re.I)
    trunk_vlan1 = bool(re.search(
        r"(port trunk (?:permit|allow-pass) vlan[^\n]*\b1\b|"
        r"switchport trunk allowed vlan[^\n]*\b1\b)", ctx.text, re.I))
    access_vlan1 = bool(re.search(r"(port access vlan 1|port default vlan 1|"
                                  r"switchport access vlan 1)", ctx.text, re.I))

    if vlan1_ip:
        out.append(_f("mid", "VLAN 1 上有三层地址",
                      "interface Vlan 1 配了 IP",
                      "把管理地址迁到专用管理 VLAN，并关闭 VLAN 1 的三层接口",
                      "VLAN 1 是所有端口的默认 VLAN，也是二层攻击的第一个落脚点。"
                      "管理面留在 VLAN 1 上，等于把设备的管理入口放进了最大的广播域。", 5))
    elif trunk_vlan1 and not access_vlan1:
        out.append(_f("low", "Trunk 放通了 VLAN 1",
                      "trunk permit vlan 包含 1",
                      "port trunk pvid vlan <专用 VLAN> + 收敛允许列表",
                      "不需要跨设备传 VLAN 1 时，把它从允许列表里摘掉能少一层"
                      "二层攻击面。", 3))
    else:
        out.append(_f("pass", "VLAN 1 未被用作业务或管理 VLAN"))

    return out


def _rule_stp(ctx: _Ctx) -> list:
    out: list = []

    access_ports = [name for name, body in ctx.iface_items()
                    if any(re.match(r"(port link-type access|port default vlan|"
                                    r"switchport mode access)", b, re.I) for b in body)]
    edged = ctx.count(r"(stp edged-port|spanning-tree portfast)")

    if not access_ports:
        return out

    if edged == 0:
        out.append(_f("mid", "接了终端的端口没配边缘端口",
                      f"{len(access_ports)} 个接入口，0 个边缘端口",
                      "interface range ...\n stp edged-port\n"
                      "（思科：spanning-tree portfast）",
                      "边缘端口不对接终端的那一侧发 BPDU，终端开机和拔插时不会触发"
                      "生成树重算，也就不会出现「插一台电脑全网卡两秒」。"
                      "配了边缘端口必须同时配 BPDU 保护，否则有人接个交换机就能"
                      "把根桥抢走。", 5))
    else:
        out.append(_f("pass", f"边缘端口已配置（{edged} 个）"))

    if edged and not ctx.has("bpdu-protection", "bpduguard"):
        out.append(_f("mid", "配了边缘端口但没开 BPDU 保护",
                      "", "stp bpdu-protection（H3C/华为）\n"
                          "spanning-tree portfast bpduguard default（思科/锐捷）",
                      "边缘端口收到 BPDU 说明下游可能被接入了交换机，"
                      "这时应该直接把端口 err-disable 掉，而不是让它参与生成树。", 5))

    if not ctx.has("stp root primary", "spanning-tree mst 1 root",
                   "spanning-tree vlan 1 root"):
        out.append(_f("low", "没有显式指定根桥",
                      "", "stp root primary（核心）/ stp root secondary（备核心）",
                      "不指定根桥，生成树会按 MAC 或优先级自动选，"
                      "可能出现「接入层设备当了根桥」这种反直觉的拓扑。", 3))
    else:
        out.append(_f("pass", "已显式指定根桥"))

    return out


def _rule_idle_ports(ctx: _Ctx) -> list:
    """闲置端口是否关闭。只统计明确的物理接口块。"""
    out: list = []

    physical = {name: body for name, body in ctx.iface_items()
                if re.search(r"\d+/\d+", name)}
    if not physical:
        return out

    shut = [n for n, body in physical.items()
            if any(b.lower() == "shutdown" for b in body)]
    active = [n for n, body in physical.items()
              if not any(b.lower() == "shutdown" for b in body)]

    if not shut and len(physical) >= 8:
        out.append(_f("low", "所有物理端口都是开启状态",
                      f"{len(physical)} 个物理接口，0 个 shutdown",
                      "interface range <闲置端口>\n shutdown",
                      "没人用的端口开着，就等于给外来设备留了接入点 —— 插上就能"
                      "拿 DHCP、进 VLAN。交付时把闲置口关掉是成本最低的一条加固。", 4))
    elif shut:
        out.append(_f("pass", f"闲置端口已关闭（{len(shut)}/{len(physical)} 个）"))

    return out


def _rule_aaa(ctx: _Ctx) -> list:
    out: list = []

    if ctx.has("radius scheme", "radius-server", "radius server", "tacacs"):
        out.append(_f("pass", "已接入外部认证服务器（RADIUS / TACACS）"))
    elif ctx.has("local-user", "username "):
        out.append(_f("low", "只有本地账号，没有集中认证",
                      "未发现 radius / tacacs 配置",
                      "radius scheme <名称> + authentication lan-access radius-scheme",
                      "本地账号的问题不是不安全，是「改一个人要登录所有设备」。"
                      "人多设备多之后，离职账号漏删是迟早的事。", 3))
    else:
        out.append(_f("mid", "没有发现任何带认证的登录方式",
                      "", "local-user <用户> class manage + service-type ssh",
                      "如果没有 AAA 也没有本地用户，说明登录认证依赖设备的默认行为，"
                      "这在测评里通常直接判不合规。", 8))

    return out


def _rule_l2_guard(ctx: _Ctx) -> list:
    out: list = []

    if ctx.has("dhcp server ip-pool", "ip dhcp pool", "ip pool"):
        if ctx.enabled(r"dhcp snooping enable") or ctx.enabled(r"ip dhcp snooping$"):
            out.append(_f("pass", "DHCP Snooping 已启用"))
        else:
            out.append(_f("mid", "设备在做 DHCP 服务，但没开 Snooping",
                          "存在 DHCP 地址池，未发现 dhcp snooping 配置",
                          "dhcp snooping enable（H3C/华为）\n"
                          "ip dhcp snooping（思科/锐捷）",
                          "不开 Snooping，任何人都能在这台交换机下面架一个 DHCP"
                          "服务器把网关改成自己 —— 这就是最经典的那类中间人攻击。", 6))

    if not ctx.has("storm-control", "storm control", "broadcast-suppression",
                   "multicast-suppression", "unicast-suppression"):
        out.append(_f("low", "没有配置广播风暴抑制",
                      "", "broadcast-suppression pps <阈值>（H3C）\n"
                          "storm-control broadcast level <阈值>（思科/锐捷）",
                      "二层环路或网卡故障引发的广播风暴能在几十秒内打满上行，"
                      "抑制不解决问题但能保住整网可用。", 3))
    else:
        out.append(_f("pass", "已配置风暴抑制"))

    return out


_RULES = (
    _rule_remote,
    _rule_snmp,
    _rule_password,
    _rule_ntp_log,
    _rule_vlan1,
    _rule_stp,
    _rule_idle_ports,
    _rule_aaa,
    _rule_l2_guard,
)


# --------------------------------------------------------------------------
# 分析
# --------------------------------------------------------------------------

#: 低于这个行数就认为用户只粘了片段，"缺失类"结论不可信
_COMPLETE_LINES = 40


def analyze(text: str) -> Report:
    """跑一遍体检，返回结构化报告。"""
    report = Report()
    body = (text or "").strip()
    if not body:
        return report

    vendor_key, evidence = detect_vendor(body)
    report.vendor_key = vendor_key
    report.vendor_name = _VENDOR_LABEL.get(vendor_key, "未识别厂商")
    report.vendor_evidence = evidence

    ctx = _parse(body, vendor_key)
    report.complete = len(ctx.lines) >= _COMPLETE_LINES
    report.stats = {
        "lines": len(ctx.lines),
        "interfaces": len(ctx.ifaces),
        "vlans": len({m for m in re.findall(r"^\s*vlan (\d+)", body, re.M)}),
        "globals": len(ctx.globals),
    }

    findings: list = []
    for rule in _RULES:
        try:
            findings.extend(rule(ctx))
        except Exception as exc:  # noqa: BLE001
            findings.append(_f("info", f"规则 {rule.__name__} 执行失败",
                               "", "", str(exc)))

    # 配置不完整时，"没查到"不能当成"没配" —— 把缺失类结论降级并标注
    if not report.complete:
        for item in findings:
            if item.level in ("high", "mid", "low") and not item.evidence:
                item.level = "low"
                item.detail = "（配置可能不完整，此项需要在完整配置上复核）" + item.detail

    report.findings = findings
    penalty = sum(item.weight for item in findings
                  if item.level in ("high", "mid", "low"))
    report.score = max(0, 100 - penalty)
    return report


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

_LEVEL_LABEL = {"high": "高", "mid": "中", "low": "低", "pass": "通过", "info": "说明"}


def render(report: Report) -> list:
    """把报告渲染成界面用的文本行。"""
    if not report.findings:
        return [
            "把设备上的配置整段粘到左边，点「开始体检」。",
            "",
            "支持的输入：",
            "  · H3C / 华为：display current-configuration",
            "  · 思科 / 锐捷：show running-config",
            "",
            "工具会自动识别厂商，不需要你选。全程离线，",
            "配置内容只在本机内存里过一遍，不会上传到任何地方。",
            "",
            "体检覆盖：远程管理面、SNMP、口令保存方式、时间与日志、",
            "VLAN 1 使用、生成树边缘端口、闲置端口、认证方式、二层防护。",
        ]

    out: list = []
    out.append("━━ 配置体检报告 ━━")
    out.append("")
    out.append(f"识别为：{report.vendor_name}")
    out.append(f"　{report.vendor_evidence}")

    stats = report.stats
    out.append(f"配置规模：{stats.get('lines', 0)} 行　|　"
               f"接口 {stats.get('interfaces', 0)} 个　|　"
               f"VLAN {stats.get('vlans', 0)} 个")
    out.append("")

    if not report.complete:
        out.append("· 注意：这段配置看起来不完整（不足 "
                   f"{_COMPLETE_LINES} 行）。")
        out.append("  「没查到」不等于「没配置」，缺失类结论请拿完整配置复核。")
        out.append("")

    out.append(f"健康分：{report.score} / 100　"
               f"（高危 {len(report.high)} · 中危 {len(report.mid)} · "
               f"低危 {len(report.low)}）")
    out.append("")

    for level in ("high", "mid", "low"):
        items = report.by_level(level)
        if not items:
            continue
        out.append(f"━━ {_LEVEL_LABEL[level]}风险（{len(items)} 项）━━")
        out.append("")
        for index, item in enumerate(items, 1):
            out.append(f"{index}. {item.title}")
            if item.evidence:
                out.append(f"   证据：{item.evidence}")
            if item.fix:
                out.append("   修复：")
                for line in item.fix.splitlines():
                    out.append(f"     {line}")
            if item.detail:
                for line in _wrap(item.detail, 62):
                    out.append(f"   {line}")
            out.append("")

    passed = report.passed
    if passed:
        out.append(f"━━ 已通过（{len(passed)} 项）━━")
        out.append("")
        for item in passed:
            tail = f"（{item.evidence}）" if item.evidence else ""
            out.append(f"  ✓ {item.title}{tail}")
        out.append("")

    infos = report.by_level("info")
    if infos:
        out.append("━━ 说明 ━━")
        out.append("")
        for item in infos:
            out.append(f"  · {item.title}：{item.detail}")
        out.append("")

    out.append("━━ 说明 ━━")
    out.append("")
    out.append("· 体检只读配置文本，不连设备、不上传，结论仅代表配置本身。")
    out.append("· 修复命令按识别出的厂商生成，下发前请核对版本与实际接口名。")
    out.append("· 分数是相对指标，用来横向比对多台设备，不是测评结论。")
    return out


def _wrap(text: str, width: int) -> list:
    """按字符宽度粗略折行，中文按 2 个宽度算。"""
    out: list = []
    cur = ""
    cur_w = 0
    for ch in text:
        w = 2 if ord(ch) > 0x2E80 else 1
        if cur_w + w > width:
            out.append(cur)
            cur = ""
            cur_w = 0
        cur += ch
        cur_w += w
    if cur:
        out.append(cur)
    return out
