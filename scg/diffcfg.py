"""双机配置一致性对比。

用于 IRF / VSU / 堆叠双机的日常核对：两台设备除了设备名、管理地址、堆叠成员
优先级之外，业务配置应当完全一致。不一致的地方就是"下次主备切换会出问题"的
地方。

不能简单做文本 diff —— 两台设备的配置里混着大量天生不同的噪声（启动时间、
配置保存时间、会话信息、接口 up/down 计数），文本 diff 会被这些淹没，真正的
业务差异反而看不见。所以这里先把配置**展平成「路径 -> 取值」**，再做集合比对。

三步：
  1. 展平：``interface X / port link-type`` -> ``trunk``；
  2. 比对：只在 A 有、只在 B 有、两边都有但取值不同；
  3. 分级：业务配置差异排前面，管理面和已知应不同的项单独归类。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 噪声：两台设备本来就会不一样，展平前先剔掉
# --------------------------------------------------------------------------

#: 整行丢弃
_NOISE_LINE = re.compile(
    r"(Last configuration change|NVRAM config last updated|"
    r"Current configuration\s*:|Building configuration|"
    r"Time since .* uptime|! No configuration change since last restart|"
    r"^!?\s*cisco\s+IOS|^!?\s*Comware|^!?\s*VRP|"
    r"^\s*version \d+\.\d+|^\s*rommon|^\s*bootrom)",
    re.I)

#: 这些路径下取值不同属于正常现象，单独归到「预期差异」
_EXPECTED = (
    (re.compile(r"^(sysname|hostname)\b", re.I), "双机的设备名本来就该不同"),
    (re.compile(r"^irf member \d+ priority\b", re.I), "堆叠成员优先级按主备规划，本该不同"),
    (re.compile(r"^switch \d+ priority\b", re.I), "堆叠成员优先级按主备规划，本该不同"),
    (re.compile(r"^stack slot \d+ priority\b", re.I), "堆叠成员优先级按主备规划，本该不同"),
)

#: 这些路径属于管理面，差异要提示但不必报警
_MANAGEMENT = (
    re.compile(r"/ ip(v6)? address\b", re.I),
    re.compile(r"^(snmp-server location|snmp-agent sys-info location)\b", re.I),
)

#: 业务上真正要命的关键字：出现在差异里就提升优先级
_CRITICAL = re.compile(
    r"(port link-type|port trunk|port access|switchport mode|switchport trunk|"
    r"switchport access|vlan \d+|ip route|network|neighbor|"
    r"link-aggregation|channel-group|eth-trunk|port-group|"
    r"stp |spanning-tree|rule \d+|acl|"
    r"password|secret|authentication|dot1x)",
    re.I)


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class DiffItem:
    kind: str        # only_a / only_b / changed
    path: str
    a: str = ""
    b: str = ""
    level: str = "high"     # high / management / expected


@dataclass
class DiffReport:
    a_label: str = "A"
    b_label: str = "B"
    a_vendor: str = ""
    b_vendor: str = ""
    a_lines: int = 0
    b_lines: int = 0
    a_keys: int = 0
    b_keys: int = 0
    items: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len({i.path for i in self.items})

    @property
    def consistency(self) -> float:
        base = max(self.a_keys, self.b_keys, 1)
        return max(0.0, 1.0 - self.total / base)

    def by_level(self, level: str) -> list:
        return [i for i in self.items if i.level == level]


# --------------------------------------------------------------------------
# 展平
# --------------------------------------------------------------------------


def _vendor_name(text: str) -> str:
    from . import audit           # 延迟导入，避免循环依赖
    key, _evidence = audit.detect_vendor(text)
    return key


def _is_view(head: str) -> bool:
    """是否是「进入一个新视图」的命令行。

    只认影响比对结构的那些：接口、VLAN、协议进程。其它一律当普通配置项，
    免得把 ``ip route-static`` 这类全局命令误当成容器。
    """
    return bool(re.match(
        r"^(interface|vlan|router|line|ip access-list|acl|"
        r"ip dhcp pool|ip pool|ip vrf|ip vpn-instance|key chain|"
        r"policy-map|class|radius|domain|user-interface|local-user)\b",
        head, re.I))


def _split_command(text: str, vendor_key: str) -> tuple:
    """把一条命令拆成「键模板」和「取值」。

    ``port trunk permit vlan 10 20`` -> ``("port trunk permit vlan <>", "10 20")``

    拆分规则直接复用回滚模块的命令正则：那里已经把每条命令的参数部分圈成了
    捕获组，把这些组从原命令里挖掉，剩下的就是稳定的键。这样才不会出现
    「``vlan 10 20`` 和 ``vlan 10 20 30`` 被当成两条不同的配置」这种退化。

    视图容器（``interface X``、``vlan 10``）例外：里面的标识符属于身份而不是
    取值，``vlan 10`` 和 ``vlan 30`` 是两个不同的 VLAN，不能算同一个键的两个值。
    """
    from . import rollback

    rules = rollback._compile_table(vendor_key)
    rule, match = rollback._match(rules, text)
    if rule is None or match is None or rule.view:
        return text, ""

    spans = []
    for index in range(1, (match.lastindex or 0) + 1):
        span = match.span(index)
        if span != (-1, -1):
            spans.append(span)
    if not spans:
        return text, ""

    key = text
    for start, end in sorted(spans, reverse=True):
        key = key[:start] + "<>" + key[end:]

    values = [match.group(i) for i in range(1, (match.lastindex or 0) + 1)
              if match.group(i)]
    return key.strip(), " ".join(v.strip() for v in values)


def flatten(text: str, vendor_key: str = "") -> dict:
    """把配置展平成 ``{键模板: 取值}``。

    同一个键重复出现时（多条 ACL 规则、多条静态路由），在键后面追加序号，
    既不互相覆盖，也能让两侧的同类条目按顺序配对。
    """
    out: dict = {}
    ctx = ""
    counter: dict = {}

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _NOISE_LINE.search(line):
            continue
        head = line.strip()
        if head in ("#", "!"):
            ctx = ""
            continue
        if head.startswith("!") or head.startswith("#"):
            continue

        # 配置导出里子命令都带缩进，没有缩进的就是顶层命令。
        # 靠这个判断比维护一张「哪些命令属于接口」的表可靠得多，
        # 也顺手解决了「上一个接口没写 # 分隔符，后面的全局命令被算进接口」。
        indented = bool(line[:1] in (" ", "\t"))

        if _is_view(head):
            ctx = head
            out.setdefault(f"[{head}]", "")
            continue

        if not indented:
            ctx = ""
            key, value = _split_command(head, vendor_key)
            out[key] = value
            continue

        key, value = _split_command(head, vendor_key)
        if ctx:
            key = f"{ctx} / {key}"

        if key in out and out[key] != value:
            counter[key] = counter.get(key, 1) + 1
            key = f"{key} #{counter[key]}"
        out[key] = value

    return out


# --------------------------------------------------------------------------
# 比对
# --------------------------------------------------------------------------


def _level_of(path: str) -> str:
    for pattern, _reason in _EXPECTED:
        if pattern.search(path):
            return "expected"
    for pattern in _MANAGEMENT:
        if pattern.search(path):
            return "management"
    return "high"


def compare(a_text: str, b_text: str,
            a_label: str = "A", b_label: str = "B") -> DiffReport:
    """比对两份配置。"""
    report = DiffReport(a_label=a_label, b_label=b_label)

    a_text = (a_text or "").strip()
    b_text = (b_text or "").strip()
    report.a_lines = len(a_text.splitlines())
    report.b_lines = len(b_text.splitlines())

    if not a_text or not b_text:
        return report

    report.a_vendor = _vendor_name(a_text)
    report.b_vendor = _vendor_name(b_text)

    a_map = flatten(a_text, report.a_vendor)
    b_map = flatten(b_text, report.b_vendor)
    report.a_keys = len(a_map)
    report.b_keys = len(b_map)

    for path in a_map.keys() - b_map.keys():
        report.items.append(DiffItem("only_a", path, a_map[path], "",
                                     _level_of(path)))
    for path in b_map.keys() - a_map.keys():
        report.items.append(DiffItem("only_b", path, "", b_map[path],
                                     _level_of(path)))
    for path in a_map.keys() & b_map.keys():
        if a_map[path] != b_map[path]:
            report.items.append(DiffItem("changed", path,
                                         a_map[path], b_map[path],
                                         _level_of(path)))

    # 业务相关且差异的排前面
    report.items.sort(key=lambda i: (
        0 if i.level == "high" else (2 if i.level == "expected" else 1),
        0 if _CRITICAL.search(i.path) else 1,
        i.path))
    return report


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render(report: DiffReport) -> list:
    if not report.items and not (report.a_lines and report.b_lines):
        return [
            "把两台设备的配置分别粘到上面两个框里，点「开始对比」。",
            "",
            "典型用途：",
            "  · IRF / VSU / 堆叠双机，核对主备业务配置是否一致",
            "  · 变更前后对比，确认这次到底改了哪些地方",
            "  · 继承别人的网络，快速看出两台设备为什么表现不一样",
            "",
            "工具会自动识别厂商，不需要你选。全程离线。",
        ]

    out: list = ["━━ 配置一致性对比 ━━", ""]
    out.append(f"A：{report.a_label}　{report.a_lines} 行　"
               f"{report.a_keys} 项配置")
    out.append(f"B：{report.b_label}　{report.b_lines} 行　"
               f"{report.b_keys} 项配置")

    if report.a_vendor and report.a_vendor != report.b_vendor:
        out.append("")
        out.append(f"· 注意：两侧识别出的厂商不一致"
                   f"（{report.a_vendor or '未知'} vs {report.b_vendor or '未知'}），"
                   "跨厂商互比时命令写法差异会全部计入差异。")
    out.append("")

    if not report.items:
        out.append("一致度：100%")
        out.append("")
        out.append("  ✓ 两份配置逐项一致，没有发现差异。")
        return out

    out.append(f"一致度：{report.consistency * 100:.1f}%　"
               f"（差异 {report.total} 项）")
    out.append("")

    sections = (
        ("high", "━━ 业务配置差异 ━━", "主备切换后行为可能不同"),
        ("management", "━━ 管理面差异 ━━", "逐台不同属正常，确认即可"),
        ("expected", "━━ 预期差异 ━━", "本来就该不一样"),
    )

    for level, title, hint in sections:
        items = report.by_level(level)
        if not items:
            continue
        out.append(f"{title}（{len(items)} 项）　{hint}")
        out.append("")
        for index, item in enumerate(items):
            if index:
                out.append("")
            out.append(f"· {item.path}")
            if item.kind == "changed":
                out.append(f"    A：{item.a}")
                out.append(f"    B：{item.b}")
            elif item.kind == "only_a":
                out.append(f"    只有 A 有：{item.a or item.path.strip('[]')}")
            else:
                out.append(f"    只有 B 有：{item.b or item.path.strip('[]')}")

    out.append("━━ 说明 ━━")
    out.append("")
    out.append("· 对比是按「路径 + 取值」做的结构化比对，不看行号，所以")
    out.append("  命令顺序不同不会被误判成差异。")
    out.append("· 启动时间、配置保存时间、版本号这类噪声已在比对前剔除。")
    out.append("· 仅 A 有 / 仅 B 有里如果出现整段接口，通常是这一侧没配这个接口，")
    out.append("  而不是配置写错了 —— 对照实物确认一下。")
    return out
