"""现网配置解析：把 `display current-configuration` 读成结构化的现状。

为什么需要这一层
---------------
工具原本只认「从零填参数」。但网工的真实动作几乎都是在**现网基础上加一点**：
给 3 楼接入加个 VLAN 40、把上联口放通、给新服务器口划个隔离。
这些操作的输入不是空白表单，是「现在是什么样」。

原来的流程要求人去设备上 `display vlan` 一个个看、再手抄回工具里。
抄写既费时间又容易错，而且抄完之后工具并不知道这些值是从哪来的。
这一层就是把那一步自动化：粘一段配置，现状直接读出来。

跟 audit.py 的区别
------------------
``audit.py`` 也解析配置，但它只要「有没有配、配得对不对」，产物是一份
体检报告。这里要的是**能被生成侧直接使用的字段**：某个口是不是 trunk、
放通了哪些 VLAN、PVID 多少。两者共用厂商识别，解析目标不同，所以分开写。

只读，不猜
---------
解析不出来的就空着。宁可让用户多填一个字段，也不要猜一个值塞进表单——
表单里的值最后会变成要下发的命令，猜错就是现网事故。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import audit

#: 接口块开始。必须吃掉整行剩余部分——``interface Vlan-interface 1`` 这种
#: 名字里带空格的，只取第一个 token 会变成 ``Vlan-interface``，把编号丢了
_RX_IFACE = re.compile(r"^interface\s+(.+?)\s*$", re.I)
#: 接口块结束（H3C / 华为用 #，思科 / 锐捷用 !）
_BLOCK_END = ("#", "!")
#: 这些是顶层命令，即使在接口块内出现也按全局处理（正常配置里不会出现）
_RX_TOP = re.compile(r"^(vlan|ip route|sysname|hostname|ntp|snmp|clock|ssh|stelnet|"
                     r"enable service|line |local-user|username|aaa )", re.I)

#: VLAN 定义：单个、范围、批量
_RX_VLAN_ONE = re.compile(r"^vlan\s+(\d+)\s*$", re.I)
_RX_VLAN_RANGE = re.compile(r"^vlan\s+(\d+)\s+to\s+(\d+)\s*$", re.I)
_RX_VLAN_BATCH = re.compile(r"^vlan\s+batch\s+(.+)$", re.I)

#: 接口内的属性。四家写法分开列，命中哪个算哪个
_IFACE_RULES: tuple = (
    (re.compile(r"^port link-type\s+(\w+)", re.I), "mode"),
    (re.compile(r"^port access vlan\s+(\d+)", re.I), "access_vlan"),
    (re.compile(r"^port default vlan\s+(\d+)", re.I), "access_vlan"),
    (re.compile(r"^port trunk permit vlan\s+(.+)$", re.I), "trunk_allowed"),
    (re.compile(r"^port trunk allow-pass vlan\s+(.+)$", re.I), "trunk_allowed"),
    (re.compile(r"^port trunk pvid(?: vlan)?\s+(\d+)", re.I), "pvid"),
    (re.compile(r"^port hybrid pvid(?: vlan)?\s+(\d+)", re.I), "pvid"),
    (re.compile(r"^port hybrid vlan\s+(\d+)\s+(\w+)", re.I), "hybrid"),
    (re.compile(r"^switchport mode\s+(\w+)", re.I), "mode"),
    (re.compile(r"^switchport access vlan\s+(\d+)", re.I), "access_vlan"),
    (re.compile(r"^switchport trunk allowed vlan\s+(.+)$", re.I), "trunk_allowed"),
    (re.compile(r"^switchport trunk native vlan\s+(\d+)", re.I), "pvid"),
    (re.compile(r"^ip address\s+(\S+)\s+(\S+)", re.I), "ip"),
    (re.compile(r"^description\s+(.+)$", re.I), "desc"),
    (re.compile(r"^name\s+(.+)$", re.I), "desc"),
)

#: 管理口识别：三层接口名
_RX_L3_IFACE = re.compile(r"^(Vlan-?interface|Vlanif|Vlan|LoopBack|Loopback)\s*\d+", re.I)

#: 静态路由
_RX_ROUTE = re.compile(r"^ip route-static\s+(.+)$|^ip route\s+(.+)$", re.I)


@dataclass
class Iface:
    """一个接口的现状。空串表示配置里没写（用的是默认值）。"""

    name: str
    mode: str = ""            # access / trunk / hybrid
    access_vlan: str = ""     # access 口属于哪个 VLAN
    trunk_allowed: str = ""   # trunk 放通列表，原样保留（"10 20 30" / "10,20,30"）
    pvid: str = ""            # trunk PVID / native VLAN
    ip: str = ""              # 三层地址，带掩码
    desc: str = ""

    @property
    def is_l3(self) -> bool:
        return bool(self.ip)

    def allowed_list(self) -> list:
        return parse_vlan_list(self.trunk_allowed)


@dataclass
class Snapshot:
    """一份现网配置读出来的东西。字段空着就是没读到。"""

    vendor: str = ""
    vendor_evidence: str = ""
    hostname: str = ""
    vlans: list = field(default_factory=list)     # [10, 20, 30]，升序
    ifaces: dict = field(default_factory=dict)    # 名字 -> Iface
    mgmt_ip: str = ""                              # 管理地址
    mgmt_vlan: str = ""                            # 管理 VLAN 的编号
    mgmt_mask: str = ""
    gateway: str = ""
    routes: list = field(default_factory=list)    # ["0.0.0.0/0 → 192.168.1.1"]
    line_count: int = 0
    complete: bool = False                        # 像不像一整份配置

    def vlan_text(self) -> str:
        """VLAN 列表转成表单里那种逗号串。"""
        return ",".join(str(v) for v in self.vlans)

    def trunk_ifaces(self) -> list:
        return [i for i in self.ifaces.values() if i.mode.lower() == "trunk"]

    def access_ifaces(self) -> list:
        return [i for i in self.ifaces.values() if i.mode.lower() == "access"]

    def uplink_iface(self):
        """猜上联口：trunk 口里放通 VLAN 最多的那个。

        只用来给界面一个默认值，不写进表单。配置里没有「上联」这个概念，
        靠 VLAN 数量猜是最接近的近似——接入口一般只放通一个 VLAN。
        ``permit vlan all`` 直接算最大，那种口一定是上联。
        """
        trunks = self.trunk_ifaces()
        if not trunks:
            return None

        def weight(iface: Iface) -> int:
            if iface.trunk_allowed.strip().lower() == "all":
                return 9999
            return len(iface.allowed_list())

        return max(trunks, key=weight)


# ==========================================================================
# 解析
# ==========================================================================

def parse(text: str) -> Snapshot:
    """把配置文本读成 Snapshot。读不出来的字段留空，不猜。"""
    source = (text or "").strip()
    snap = Snapshot(line_count=len(source.splitlines()))
    if not source:
        return snap

    vendor, evidence = audit.detect_vendor(source)
    snap.vendor = vendor
    snap.vendor_evidence = evidence

    current: Iface | None = None
    route_lines: list = []

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue

        # 分块结束符：H3C / 华为的 #，思科 / 锐捷的 !
        if line in _BLOCK_END or line.startswith("!"):
            current = None
            continue

        # 退出视图
        if line.lower() in ("quit", "exit", "return"):
            current = None
            continue

        # 主机名
        m = re.match(r"^(?:sysname|hostname)\s+(\S+)", line, re.I)
        if m:
            snap.hostname = m.group(1)
            current = None
            continue

        # VLAN 定义
        m = _RX_VLAN_BATCH.match(line)
        if m:
            snap.vlans.extend(parse_vlan_list(m.group(1)))
            current = None
            continue
        m = _RX_VLAN_RANGE.match(line)
        if m:
            snap.vlans.extend(parse_vlan_list(f"{m.group(1)} to {m.group(2)}"))
            current = None
            continue
        m = _RX_VLAN_ONE.match(line)
        if m:
            snap.vlans.append(int(m.group(1)))
            current = None
            continue

        # 静态路由
        m = _RX_ROUTE.match(line)
        if m:
            route_lines.append((m.group(1) or m.group(2) or "").strip())
            current = None
            continue

        # 接口
        m = _RX_IFACE.match(line)
        if m:
            name = m.group(1)
            current = snap.ifaces.get(name) or Iface(name=name)
            snap.ifaces[name] = current
            continue

        # 顶层命令（接口块之外，或者是全局类命令）
        if _RX_TOP.match(line):
            current = None

        # 接口内属性
        if current is not None:
            _apply_iface_line(current, line)

    snap.vlans = sorted({v for v in snap.vlans if 1 <= v <= 4094})
    snap.routes = _summarize_routes(route_lines)
    _fill_mgmt(snap)
    # 有十几个 VLAN 或接口、又有主机名，基本可以认定是整份配置
    snap.complete = bool(snap.vlans or snap.ifaces) and snap.line_count >= 10
    return snap


def _apply_iface_line(iface: Iface, line: str) -> None:
    for rx, attr in _IFACE_RULES:
        m = rx.match(line)
        if not m:
            continue
        if attr == "ip":
            iface.ip = f"{m.group(1)} {m.group(2)}"
        elif attr == "hybrid":
            # port hybrid vlan 10 untagged —— 不参与 trunk/access 判断，跳过
            continue
        else:
            setattr(iface, attr, m.group(1).strip())
        return


def _fill_mgmt(snap: Snapshot) -> None:
    """找管理地址：三层接口里带 IPv4 地址的那个。

    优先 Vlan-interface / Vlanif / Vlan，因为交换机管理地址基本都挂在这儿。
    """
    candidates = [i for i in snap.ifaces.values() if i.is_l3]
    if not candidates:
        return

    preferred = [i for i in candidates if _RX_L3_IFACE.match(i.name)]
    target = (preferred or candidates)[0]

    parts = target.ip.split()
    snap.mgmt_ip = parts[0] if parts else ""
    snap.mgmt_mask = parts[1] if len(parts) > 1 else ""

    m = re.search(r"(\d+)", target.name)
    if m and re.match(r"^Vlan", target.name, re.I):
        snap.mgmt_vlan = m.group(1)

    # 默认路由当网关
    for line in snap.routes:
        if line.startswith("0.0.0.0/0"):
            tail = line.split("→")[-1].strip()
            if tail:
                snap.gateway = tail.split()[0]
                break


def _summarize_routes(lines: list) -> list:
    """把静态路由压成「目的 → 下一跳」的可读形式。"""
    out: list = []
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        dest = parts[0]
        # H3C / 华为：ip route-static 0.0.0.0 0 192.168.1.1
        # 思科 / 锐捷：ip route 0.0.0.0 0.0.0.0 192.168.1.1
        mask = parts[1] if len(parts) > 1 else ""
        nxt = parts[2] if len(parts) > 2 else ""
        if not nxt:
            continue
        prefix = _to_prefix(dest, mask)
        out.append(f"{prefix} → {nxt}")
    return out


def _to_prefix(dest: str, mask: str) -> str:
    """点分掩码转前缀长度。转不了就原样保留两个值。"""
    if mask.isdigit():
        return f"{dest}/{mask}"
    bits = mask.split(".")
    if len(bits) != 4:
        return f"{dest} {mask}".strip()
    try:
        count = sum(bin(int(b)).count("1") for b in bits)
        return f"{dest}/{count}"
    except ValueError:
        return f"{dest} {mask}".strip()


def parse_vlan_list(text: str) -> list:
    """解析 VLAN 列表写法。

    要兼容四种：``10 20 30``（H3C permit）、``10,20,30``（思科 allowed）、
    ``10 to 20``（H3C 范围）、``10-20``（手写范围）。0 和超范围的丢掉。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    cleaned = cleaned.replace(" to ", "-").replace(" To ", "-")
    out: list = []
    for part in re.split(r"[,\s]+", cleaned):
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if m:
            low, high = int(m.group(1)), int(m.group(2))
            if low > high:
                low, high = high, low
            # 范围过大基本是写错了，不展开，避免生成几万个数
            if high - low > 512:
                continue
            out.extend(range(max(1, low), min(4094, high) + 1))
        elif part.isdigit():
            value = int(part)
            if 1 <= value <= 4094:
                out.append(value)
    return out


# ==========================================================================
# 给表单的建议值
# ==========================================================================

def suggest(vendor_key: str, scenario_key: str, snap: Snapshot) -> dict:
    """按场景给一组保守的建议参数。

    只填「配置里读得到、而且语义明确」的字段。读不到就不填，
    让用户自己来——比填个错值好。
    """
    if snap is None:
        return {}

    rules = {
        "vlan_port": _suggest_vlan_port,
        "mgmt": _suggest_mgmt,
        "static_route": _suggest_route,
        "port_isolation": _suggest_isolation,
    }
    handler = rules.get(scenario_key)
    if handler is None:
        return {}
    try:
        return {k: v for k, v in handler(snap).items() if str(v).strip()}
    except Exception:  # noqa: BLE001 —— 建议值算不出来不该影响主流程
        return {}


def _suggest_vlan_port(snap: Snapshot) -> dict:
    """VLAN 场景：带上现有 VLAN 列表和上联口的放通情况。"""
    out: dict = {}
    if snap.vlans:
        out["vlans"] = snap.vlan_text()

    uplink = snap.uplink_iface()
    if uplink is not None:
        allowed = uplink.allowed_list()
        if allowed:
            out["allowed"] = ",".join(str(v) for v in allowed)
        if uplink.pvid:
            out["pvid"] = uplink.pvid
    return out


def _suggest_mgmt(snap: Snapshot) -> dict:
    out: dict = {}
    if snap.hostname:
        out["sysname"] = snap.hostname
    if snap.mgmt_vlan:
        out["mgmt_vlan"] = snap.mgmt_vlan
    if snap.mgmt_ip:
        out["mgmt_ip"] = snap.mgmt_ip
    if snap.mgmt_mask:
        out["mask"] = snap.mgmt_mask
    if snap.gateway:
        out["gateway"] = snap.gateway
    return out


def _suggest_route(snap: Snapshot) -> dict:
    if not snap.routes:
        return {}
    return {"routes": "\n".join(snap.routes)}


def _suggest_isolation(snap: Snapshot) -> dict:
    """端口隔离：把上联口填进去（隔离场景需要一个上行口）。"""
    uplink = snap.uplink_iface()
    if uplink is None:
        return {}
    return {"uplink": uplink.name}


# ==========================================================================
# 给界面看的摘要
# ==========================================================================

def summary_lines(snap: Snapshot) -> list:
    """一段人能读的现网摘要。界面上直接贴，不做成表格。"""
    if snap is None:
        return ["没有可用的解析结果"]

    from .vendors import get_vendor

    try:
        vendor_name = get_vendor(snap.vendor).name if snap.vendor else "未识别"
    except ValueError:
        vendor_name = snap.vendor or "未识别"

    out = [f"厂商：{vendor_name}（{snap.vendor_evidence}）"]
    if snap.hostname:
        out.append(f"主机名：{snap.hostname}")
    out.append(f"共 {snap.line_count} 行配置，识别出 {len(snap.ifaces)} 个接口")

    if snap.vlans:
        text = snap.vlan_text()
        if len(text) > 80:
            text = text[:80] + f"…（共 {len(snap.vlans)} 个）"
        out.append(f"VLAN：{text}")
    else:
        out.append("VLAN：没读到")

    trunks = snap.trunk_ifaces()
    if trunks:
        out.append("Trunk 口：")
        for iface in trunks[:6]:
            allowed = iface.trunk_allowed or "（全部）"
            pvid = f"，PVID {iface.pvid}" if iface.pvid else ""
            out.append(f"  {iface.name} → 放通 {allowed}{pvid}")
        if len(trunks) > 6:
            out.append(f"  ……另有 {len(trunks) - 6} 个")
    else:
        out.append("Trunk 口：没读到")

    accesses = snap.access_ifaces()
    if accesses:
        out.append(f"Access 口：{len(accesses)} 个")

    if snap.mgmt_ip:
        out.append(f"管理地址：{snap.mgmt_ip} {snap.mgmt_mask}"
                   + (f"（VLAN {snap.mgmt_vlan}）" if snap.mgmt_vlan else ""))
    if snap.gateway:
        out.append(f"默认网关：{snap.gateway}")
    if snap.routes:
        out.append(f"静态路由：{len(snap.routes)} 条")
    return out
