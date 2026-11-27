"""配置语法校对：五类，全部不联网。

跟 `scg.audit` 不是一回事
------------------------
``audit.py`` 看的是**安全**——SNMP 团体名、VLAN1 管理、NTP、空闲端口，
那是等保和验收的视角。这里看的是**语法与结构**——命令有没有跑在对的视图里、
基础 ACL 是不是带了协议字段、端口编号符不符合厂商惯例、关键字有没有拼错。

前者回答「这台设备安不安全」，后者回答「这段配置能不能跑」。
两个都过一遍，才算看全了。

为什么值得单独做一个规则层
-------------------------
「粘贴一段配置问哪里不对」这个需求，最容易想到的做法是直接丢给模型。
但真实排错里最常见的错误是**可判定**的，不是需要理解的：

* 这段是华为语法，可他选的是思科
* ``port link-type`` 跑在系统视图外面了
* 基础 ACL 里写了协议字段，设备会直接报语法错
* 锐捷配了 SSH 却没开服务端，一定连不上
* ``port link-trpe`` 拼错了

这些用正则和一张小表就能查，毫秒级、零成本、断网也能用。模型该补的是
「两段配置互相冲突」「缺配套命令」这类需要读懂意图的问题，不是这些。

``verify.FIXED`` 里那 7 条修正（ACL 编号区间、``mirror`` 漏 ``ing``、
锐捷 ssh-server、时区冒号、接口名被空格劈断……）全部翻译成了这里的规则。
那些是拿官方手册逐条核出来的，比模型的记忆可靠。

边界
----
只报有依据的问题。每条 Finding 必须带 ``evidence``——指到具体行号或
具体缺失的命令。网工不会因为一句「这里可能有问题」就去改现网配置。
拿不准的一律降到 info，或者干脆不报。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .. import explain

ERROR = "error"
WARN = "warn"
INFO = "info"

#: 单个文件最多报多少条「未识别」，防止粘一整份配置刷出一屏噪音
MAX_UNKNOWN = 15


@dataclass(frozen=True)
class Finding:
    """一条问题。``line`` 为 0 表示整段文本级别的问题。"""

    line: int
    text: str
    level: str
    rule: str
    message: str
    evidence: str
    fix: str = ""


@dataclass(frozen=True)
class _Line:
    no: int          # 1-based 行号
    raw: str         # 原样，保留缩进
    body: str        # 去首尾空白
    is_note: bool    # 注释 / 空行


# ==========================================================================
# 入口
# ==========================================================================

def check(vendor_key: str, text: str) -> tuple:
    """跑全部离线规则。

    返回 ``(findings, unmatched)``：
    ``findings`` 是问题清单（按行号排序），``unmatched`` 是识别不出语法的行。
    """
    lines = _prepare(text)
    if not lines:
        return [], []

    findings: list = []
    findings += _r1_vendor(vendor_key, lines)
    findings += _r2_view(vendor_key, lines)
    findings += _r3_traps(vendor_key, lines)
    findings += _r4_ports(vendor_key, lines)

    findings.sort(key=lambda f: (f.line, _level_rank(f.level)))
    unmatched = _r5_unknown(vendor_key, lines)
    return findings, unmatched


def summarize(findings: list, unmatched: list) -> dict:
    counts = {ERROR: 0, WARN: 0, INFO: 0}
    for item in findings:
        counts[item.level] = counts.get(item.level, 0) + 1
    counts["unknown"] = len(unmatched)
    return counts


def _level_rank(level: str) -> int:
    return {ERROR: 0, WARN: 1, INFO: 2}.get(level, 3)


def _prepare(text: str) -> list:
    out: list = []
    for i, raw in enumerate((text or "").splitlines(), start=1):
        body = raw.strip()
        out.append(_Line(i, raw.rstrip(), body,
                         is_note=(not body) or body[0] in ("#", "!")))
    return [ln for ln in out if not ln.is_note]


# ==========================================================================
# R1 厂商语法错配
# ==========================================================================

#: (正则, 语法族得分, 具体厂商或 None)
#: 族只分三个：comware(H3C) / vrp(华为) / ios(思科+锐捷)。
#: 思科和锐捷的二层语法几乎一致，硬分只会误报，所以合成一族。
_SIGNS: tuple = (
    (r"^system-view\b", 3, "comware", None),
    (r"^configure terminal\b", 3, "ios", None),
    (r"^sysname\b", 2, "comware", None),
    (r"^hostname\b", 2, "ios", None),
    (r"^port trunk permit\b", 3, "comware", "h3c"),
    (r"^port access vlan\b", 2, "comware", "h3c"),
    (r"^port trunk allow-pass\b", 3, "comware", "huawei"),
    (r"^port default vlan\b", 2, "comware", "huawei"),
    (r"^port link-type\b", 2, "comware", None),
    (r"^switchport mode\b", 2, "ios", None),
    (r"^switchport trunk allowed\b", 2, "ios", None),
    (r"^switchport access vlan\b", 2, "ios", None),
    (r"^display\b", 1, "comware", None),
    (r"^show\b", 1, "ios", None),
    (r"^quit\b", 1, "comware", None),
    (r"^Bridge-Aggregation\b", 2, "comware", "h3c"),
    (r"^Eth-Trunk\b", 2, "comware", "huawei"),
    (r"^interface Vlan-interface\b", 2, "comware", "h3c"),
    (r"^interface Vlanif\b", 2, "comware", "huawei"),
    (r"^interface Vlan\s*\d", 2, "ios", None),
    (r"^AggregatePort\b", 2, "ios", "ruijie"),
    (r"^vrrp vrid\b", 1, "comware", None),
)

#: 用户选的厂商 → 它属于哪一族。
#: H3C 的 Comware 与华为的 VRP 同源，二层写法都是 port link-type，
#: 所以先按「命令体系」分两族；族内再用各自独有的关键字细分厂商。
_VENDOR_FAMILY = {"h3c": "comware", "huawei": "comware", "cisco": "ios", "ruijie": "ios"}

_FAMILY_NAME = {"comware": "H3C / 华为", "ios": "思科 / 锐捷"}
_VENDOR_NAME = {"h3c": "H3C", "huawei": "华为", "cisco": "思科", "ruijie": "锐捷"}


def _r1_vendor(vendor_key: str, lines: list) -> list:
    user_family = _VENDOR_FAMILY.get(vendor_key)
    if user_family is None:
        return []

    family_score = {"comware": 0, "ios": 0}
    vendor_score = {"h3c": 0, "huawei": 0, "cisco": 0, "ruijie": 0}

    for ln in lines:
        for pattern, weight, family, vendor in _SIGNS:
            if re.match(pattern, ln.body, re.I):
                family_score[family] += weight
                if vendor:
                    vendor_score[vendor] += weight

    top_family = max(family_score, key=family_score.get)
    top_score = family_score[top_family]
    user_score = family_score.get(user_family, 0)

    # 跨族：这是最要命的错配，直接讲清楚
    if top_family != user_family and top_score >= 2 and top_score - user_score >= 2:
        detected = _FAMILY_NAME.get(top_family, top_family)
        peer = max(("h3c", "huawei"), key=vendor_score.get)
        if vendor_score[peer] >= 3:
            detected = _VENDOR_NAME[peer]
        return [Finding(
            line=0,
            text="",
            level=ERROR,
            rule="R1-vendor",
            message=f"这段配置是 {detected} 的语法，但当前选的是 {_VENDOR_NAME.get(vendor_key, vendor_key)}",
            evidence=f"{detected} 特征得分 {top_score}，当前厂商特征得分 {user_score}",
            fix="切换右上角厂商后重新生成；如果是故意粘他厂配置做对照，可忽略这条",
        )]

    # 族内：H3C 与华为的差异比跨族小，但 permit / allow-pass、access / default 这种
    # 一旦搞错就是设备直接报错，所以也要提，只是降到 warn
    if user_family == "comware" and vendor_key in ("h3c", "huawei"):
        other = "huawei" if vendor_key == "h3c" else "h3c"
        if vendor_score[other] >= 3 and vendor_score[vendor_key] == 0:
            return [Finding(
                line=0,
                text="",
                level=WARN,
                rule="R1-vendor",
                message=f"这段配置更像 {_VENDOR_NAME[other]} 的写法",
                evidence=(f"{_VENDOR_NAME[other]} 独有关键字命中 {vendor_score[other]} 分，"
                          f"{_VENDOR_NAME[vendor_key]} 独有关键字 0 分"),
                fix="H3C 用 port trunk permit、华为用 port trunk allow-pass，核对一下再下发",
            )]

    return []


# ==========================================================================
# R2 视图错位
# ==========================================================================

#: 只能在接口视图里执行的命令。只收「确定无疑」的，宁可少报不要误报。
_IFACE_ONLY: tuple = (
    r"port link-type\b",
    r"port access vlan\b",
    r"port default vlan\b",
    r"port trunk permit\b",
    r"port trunk allow-pass\b",
    r"port trunk pvid\b",
    r"switchport mode\b",
    r"switchport access vlan\b",
    r"switchport trunk\b",
    r"switchport port-security\b",
    r"ip address\b",
    r"stp edged-port\b",
    r"stp edged-port enable\b",
    r"spanning-tree portfast\b",
    r"spanning-tree bpduguard\b",
    r"spanning-tree bpdufilter\b",
    r"port-isolate enable\b",
    r"port-isolate l3 enable\b",
    r"loopback-detection enable\b",
    r"dhcp snooping trust\b",
    r"arp detection trust\b",
    r"ip verify source\b",
    r"broadcast-suppression\b",
    r"storm-control\b",
    r"qos lr\b",
)

_IFACE_ONLY_RX = tuple(re.compile(p, re.I) for p in _IFACE_ONLY)
_RX_ENTER_SYSTEM = re.compile(r"^(system-view|configure terminal)$", re.I)
_RX_ENTER_IFACE = re.compile(r"^interface\s+(\S+)", re.I)
_RX_LEAVE = re.compile(r"^(quit|exit)$", re.I)
_RX_END = re.compile(r"^(end|return)$", re.I)


def _r2_view(vendor_key: str, lines: list) -> list:
    """跟踪视图栈，抓「命令跑错视图」这一类错。

    初始视图是最容易搞错的地方
    --------------------------
    用户粘过来的东西通常是 ``display current-configuration`` 的输出，
    那里面**没有** ``system-view``——配置文件的每一段本身就已经在系统视图下。
    如果一律从 user 视图起算，所有接口下的命令都会被判成「位置错误」，
    整屏误报。

    所以先看全文有没有 ``system-view`` / ``configure terminal``：

    * 有 → 这是手工敲命令的过程记录，从 user 视图起算
    * 没有 → 这是配置文件，从 system 视图起算

    视图栈用栈而不是一个变量，是因为嵌套真实存在（接口下再进 port-group）。
    """
    has_enter = any(_RX_ENTER_SYSTEM.match(ln.body) for ln in lines)
    findings: list = []
    view = "user" if has_enter else "system"
    stack: list = []

    for ln in lines:
        body = ln.body

        if _RX_ENTER_SYSTEM.match(body):
            view, stack = "system", []
            continue
        if _RX_END.match(body):
            view, stack = "user", []
            continue

        m = _RX_ENTER_IFACE.match(body)
        if m:
            # 已经在接口视图里又进下一个接口（思科风格，中间不写 exit），
            # 是平级替换，不再压栈，否则 quit 一次会退过头
            if view != "iface":
                stack.append(view)
            view = "iface"
            continue

        if _RX_LEAVE.match(body):
            view = stack.pop() if stack else ("system" if view == "iface" else "user")
            continue

        if view == "iface":
            continue

        for rx in _IFACE_ONLY_RX:
            if rx.match(body):
                where = "系统视图" if view == "system" else "用户视图（还没进配置模式）"
                findings.append(Finding(
                    line=ln.no,
                    text=body,
                    level=ERROR,
                    rule="R2-view",
                    message=f"这条命令必须在接口视图下执行，当前在{where}",
                    evidence=f"往上找没有生效的 interface 语句，命令落在{where}里",
                    fix="先 interface <接口名> 进入接口视图，再执行本命令",
                ))
                break

    return findings


# ==========================================================================
# R3 已知陷阱：verify.FIXED 那 7 条的可执行版本
# ==========================================================================

_RX_ACL_START = re.compile(r"^acl\s+(?:number\s+)?(basic\s+|advanced\s+)?(\d+)", re.I)
_RX_ACL_IOS = re.compile(r"^(?:access-list\s+(\d+)|ip access-list\s+(standard|extended)\s+(\S+))", re.I)
_RX_RULE = re.compile(r"\b(permit|deny)\b", re.I)
_RX_PROTO = re.compile(r"\b(permit|deny)\s+(ip|tcp|udp|icmp|gre|ospf|igmp)\b", re.I)
_RX_DST = re.compile(r"\b(destination|destination-port)\b", re.I)

_RX_TZ_BAD = re.compile(r"clock\s+timezone\b.*[+-]\d{1,2}:\d{2}", re.I)
_RX_MIRROR_TYPO = re.compile(r"\bmirror\s+to\s+observe-port\b", re.I)
_RX_HW_BOTH = re.compile(r"\bmirroring\b.*\bboth\b", re.I)
_RX_RJ_TELNET = re.compile(r"\bno\s+service\s+telnet\b(?!-)", re.I)
_RX_SILENT_ORPHAN = re.compile(r"^silent-interface\s+(Vlan-interface|GigabitEthernet|Ten-GigabitEthernet)\s*$", re.I)
_RX_SILENT_NUM = re.compile(r"^silent-interface\s+\d+\s*$", re.I)
_RX_STACK = re.compile(r"^(irf\s|stack-port|switch virtual domain|vsu\b|stack\s+slot)", re.I)


def _r3_traps(vendor_key: str, lines: list) -> list:
    findings: list = []
    findings += _trap_acl(vendor_key, lines)
    findings += _trap_mirror(vendor_key, lines)
    findings += _trap_ssh(vendor_key, lines)
    findings += _trap_misc(vendor_key, lines)
    return findings


def _acl_is_basic(acl_id: int, vendor_key: str) -> bool:
    """按厂商各自的编号体系判断是不是基础 / 标准 ACL。

    这是 ``verify.FIXED`` 第二条：思科 / 锐捷的 100-199 是扩展 ACL，
    跟 H3C / 华为的 2000-2999 基础 ACL 不在同一个体系里。
    用同一套区间判断会让「输入 ACL 100」被当成标准 ACL，
    协议字段被静默丢弃，策略看着配上了其实没生效。
    """
    if vendor_key in ("cisco", "ruijie"):
        return 1 <= acl_id <= 99 or 1300 <= acl_id <= 1999
    return 2000 <= acl_id <= 2999


def _trap_acl(vendor_key: str, lines: list) -> list:
    """基础 ACL 里不允许出现协议字段和目的地址。"""
    findings: list = []
    current: int | None = None      # 当前所处的 ACL 编号
    basic = False

    for ln in lines:
        body = ln.body

        if re.match(r"^(quit|exit|end)$", body, re.I):
            current = None
            continue

        # 行内式必须先判：access-list 10 permit ip any any 是一整条命令，
        # 不是 ACL 块的开头。如果先走块式匹配，这一行会被当成块头吃掉，
        # 规则体永远跑不到。
        inline = re.match(r"^access-list\s+(\d+)\s+(.+)$", body, re.I)
        if inline:
            acl_id = int(inline.group(1))
            if (_acl_is_basic(acl_id, vendor_key)
                    and (_RX_PROTO.match(inline.group(2))
                         or _RX_DST.search(inline.group(2)))):
                findings.append(_acl_finding(ln, acl_id, "行内式 access-list"))
            continue

        m = _RX_ACL_START.match(body)
        if m:
            current = int(m.group(2))
            basic = _acl_is_basic(current, vendor_key)
            continue

        m = _RX_ACL_IOS.match(body)
        if m:
            if m.group(1):
                current = int(m.group(1))
                basic = _acl_is_basic(current, vendor_key)
            else:
                current = 0
                basic = (m.group(2) or "").lower() == "standard"
            continue

        if current is not None and basic and _RX_RULE.search(body):
            if _RX_PROTO.search(body) or _RX_DST.search(body):
                findings.append(_acl_finding(ln, current, "ACL 块内"))

    return findings


def _acl_finding(ln: _Line, acl_id: int, where: str) -> Finding:
    return Finding(
        line=ln.no,
        text=ln.body,
        level=ERROR,
        rule="R3-acl",
        message=f"基础（标准）ACL {acl_id} 只能匹配源地址，不允许出现协议字段或目的地址",
        evidence=f"{where}的规则里找到了协议关键字或 destination 字段",
        fix="去掉 ip / tcp / udp 等协议字段和目的地址；如果确实要匹配目的，改用高级（扩展）ACL",
    )


def _trap_mirror(vendor_key: str, lines: list) -> list:
    findings: list = []
    for ln in lines:
        if _RX_MIRROR_TYPO.search(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=ERROR, rule="R3-mirror",
                message="关键字少了 ing：正确写法是 mirroring to observe-port",
                evidence="匹配到 mirror to observe-port，Comware 里没有这个命令",
                fix="改成 mirroring to observe-port <索引> inbound",
            ))
        elif _RX_HW_BOTH.search(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=WARN, rule="R3-mirror",
                message="华为 VLAN 镜像只支持入方向，both 超出主流版本支持范围",
                evidence="镜像命令里出现了 both",
                fix="改成 inbound；出方向镜像请改用端口镜像",
            ))
    return findings


def _trap_ssh(vendor_key: str, lines: list) -> list:
    """锐捷 RGOS 默认关闭 SSH 服务端，只配账号和密钥是连不上的。"""
    if vendor_key != "ruijie":
        return []

    used = False
    has_server = False
    first_line = 0

    for ln in lines:
        low = ln.body.lower()
        if "enable service ssh-server" in low:
            has_server = True
            continue
        if "ssh" in low or "rsa local-key-pair" in low or low.startswith("line vty"):
            used = True
            if not first_line:
                first_line = ln.no

    if not used or has_server:
        return []

    return [Finding(
        line=first_line, text="", level=ERROR, rule="R3-ssh",
        message="配了 SSH 相关命令，但没有开启 SSH 服务端，这样一定连不上",
        evidence="全文有 ssh / rsa / line vty 配置，但没有 enable service ssh-server",
        fix="补一条 enable service ssh-server（全局配置模式下执行）",
    )]


def _trap_misc(vendor_key: str, lines: list) -> list:
    findings: list = []
    for ln in lines:
        if _RX_TZ_BAD.search(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=ERROR, rule="R3-timezone",
                message="时区命令不接受 UTC+08:00 这种带符号冒号的写法",
                evidence="时间偏移写成了 ±HH:MM 形式",
                fix="H3C / 华为：clock timezone CST add 08:00:00；思科 / 锐捷：clock timezone CST 8",
            ))
        elif _RX_RJ_TELNET.search(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=WARN, rule="R3-telnet",
                message="RGOS 里没有 no service telnet 这条命令",
                evidence="锐捷关闭 Telnet 服务端的正确命令是 no enable service telnet-server",
                fix="改成 no enable service telnet-server",
            ))
        elif _RX_SILENT_ORPHAN.search(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=ERROR, rule="R3-split",
                message="接口名被空格劈断了，这条命令不完整",
                evidence="silent-interface 后面只剩接口类型，缺少接口编号",
                fix="合并成一行：silent-interface Vlan-interface 10",
            ))
        elif _RX_SILENT_NUM.match(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=WARN, rule="R3-split",
                message="这一行像是上一条命令被空格劈断后剩下的半截",
                evidence="silent-interface 后面只有一个数字",
                fix="合并回上一条：silent-interface <接口类型> <编号>",
            ))
        elif _RX_STACK.match(ln.body):
            findings.append(Finding(
                line=ln.no, text=ln.body, level=INFO, rule="R3-stack",
                message="堆叠 / 设备虚拟化命令会重启设备并中断业务",
                evidence="出现 irf / stack-port / vsu / switch virtual domain 类命令",
                fix="确认在维护窗口内，两台设备分别生成、分别执行，先配从设备",
            ))
    return findings


# ==========================================================================
# R4 端口编号惯例
# ==========================================================================

_RX_IFACE_NAME = re.compile(
    r"\b((?:Ten-)?GigabitEthernet|XGE|GE|Gi|Te|Fa|FastEthernet|Ethernet|Eth)"
    r"\s*(\d+(?:/\d+){1,2})", re.I)


def _r4_ports(vendor_key: str, lines: list) -> list:
    findings: list = []
    seen: set = set()

    for ln in lines:
        for m in _RX_IFACE_NAME.finditer(ln.body):
            number = m.group(2)
            parts = number.split("/")
            if len(parts) != 3:
                if len(parts) == 2 and vendor_key in ("h3c", "huawei"):
                    key = ("2seg", number)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(Finding(
                        line=ln.no, text=ln.body, level=INFO, rule="R4-port",
                        message=f"{number} 是两段编号，H3C / 华为 一般是三段（如 1/0/1）",
                        evidence=f"接口名 {m.group(0)}",
                        fix="核对设备面板与 interface brief 的实际输出",
                    ))
                continue

            if vendor_key == "h3c" and parts[0] == "0":
                key = ("h3c-0", number)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    line=ln.no, text=ln.body, level=WARN, rule="R4-port",
                    message=f"{number} 的首段是 0，H3C 一般从 1/0/1 起",
                    evidence="H3C 的接口编号首段是设备 / 槽位号，单机通常为 1",
                    fix="核对一下，别把华为的 0/0/1 填给 H3C",
                ))
            elif vendor_key == "huawei" and parts[0] == "1":
                key = ("hw-1", number)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    line=ln.no, text=ln.body, level=INFO, rule="R4-port",
                    message=f"{number} 的首段是 1，华为单机一般从 0/0/1 起",
                    evidence="若不是堆叠第二台，这个编号可能对不上设备",
                    fix="用 display interface brief 核对实际接口名",
                ))

    return findings


# ==========================================================================
# R5 未识别语法：拿 explain 的 170 条正则当白名单，再用 difflib 找近似
# ==========================================================================

_PROBE_CACHE: dict = {}


def _probes(vendor_key: str) -> list:
    """把该厂商全部语法原型的「固定部分」提取成候选串。

    syntax 里带占位符：``port link-type { access | trunk }``。
    直接拿去比对，相似度会被占位符拉低，所以只留固定关键字：
    ``port link-type``。再取前两个词，跟用户行做同样的处理后比较——
    取三个词反而会让带参数的行（``port link-type trunk``）相似度掉下来。
    """
    if vendor_key in _PROBE_CACHE:
        return _PROBE_CACHE[vendor_key]

    out: set = set()
    for doc in explain.docs_for(vendor_key):
        for form in doc.forms_of(vendor_key):
            fixed = re.sub(r"[<{\[].*?[>}\]]", " ", form.syntax or "")
            fixed = re.sub(r"[|]", " ", fixed)
            head = " ".join(fixed.split()[:2]).strip().lower()
            if head:
                out.add(head)
    result = sorted(out)
    _PROBE_CACHE[vendor_key] = result
    return result


def _r5_unknown(vendor_key: str, lines: list) -> list:
    """挑出 explain 认不出的行，并给一个最接近的候选。

    这条规则有两个作用：一是抓拼写错误和版本差异，二是当覆盖率探针——
    如果真实用户粘的配置大量落到这里，说明 ``explain.CATALOG`` 该补概念了。
    """
    probes = _probes(vendor_key)
    out: list = []
    seen: set = set()

    for ln in lines:
        body = ln.body
        if len(body) < 3:
            continue
        if explain.match(vendor_key, body) is not None:
            continue
        # 视图切换、分隔符这类不承载配置意义的行不算未识别
        if re.match(r"^(quit|exit|end|return|system-view|configure terminal|undo\b)", body, re.I):
            continue
        if body in seen:
            continue
        seen.add(body)

        head = " ".join(body.split()[:2]).lower()
        suggest = ""
        if probes:
            match = difflib.get_close_matches(head, probes, n=1, cutoff=0.75)
            if match:
                suggest = match[0]

        out.append({
            "line": ln.no,
            "text": body,
            "suggest": suggest,
        })
        if len(out) >= MAX_UNKNOWN:
            break

    return out
