"""厂商模块共用：结果构建器与各类输入的解析工具。"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from .. import ports
from ..model import Block, GenResult


# --------------------------------------------------------------------------
# 结果构建
# --------------------------------------------------------------------------

class Builder:
    """按区块累积命令，最后产出 GenResult。"""

    def __init__(self, vendor_name: str, scenario_name: str):
        self.result = GenResult(vendor_name=vendor_name, scenario_name=scenario_name)

    def block(self, title: str, lines, comment: str = "") -> "Builder":
        lines = [ln for ln in (lines or []) if ln is not None]
        if lines:
            self.result.blocks.append(Block(title, list(lines), comment))
        return self

    def raw(self, lines, comment: str = "") -> "Builder":
        return self.block("", lines, comment)

    def note(self, text: str) -> "Builder":
        self.result.notes.append(text)
        return self

    def warn(self, text: str) -> "Builder":
        self.result.warnings.append(text)
        return self

    def done(self) -> GenResult:
        return self.result


# --------------------------------------------------------------------------
# 基础解析
# --------------------------------------------------------------------------

def split_list(text: str) -> list[str]:
    """按逗号 / 分号 / 空白拆成片段。"""
    if not text:
        return []
    return [t for t in re.split(r"[,，;；\s]+", str(text).strip()) if t]


def parse_vlans(spec: str) -> list[int]:
    """``10,20,30-32`` -> ``[10, 20, 30, 31, 32]``（去重保序）。"""
    out: list[int] = []
    for chunk in split_list(spec):
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            if not (a.strip().isdigit() and b.strip().isdigit()):
                raise ValueError(f"VLAN 范围写法有误：{chunk}")
            lo, hi = int(a), int(b)
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        elif chunk.isdigit():
            out.append(int(chunk))
        else:
            raise ValueError(f"VLAN 写法有误：{chunk}")

    seen: set[int] = set()
    uniq: list[int] = []
    for v in out:
        if not 1 <= v <= 4094:
            raise ValueError(f"VLAN 号超出范围 1-4094：{v}")
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def parse_vlan_names(spec: str) -> dict[int, str]:
    """``10:Sales,20:IT`` -> ``{10: 'Sales', 20: 'IT'}``。"""
    names: dict[int, str] = {}
    for chunk in split_list(spec):
        m = re.match(r"^(\d+)\s*[:：]\s*(.+)$", chunk)
        if m:
            names[int(m.group(1))] = m.group(2).strip()
    return names


def parse_pairs(spec: str) -> dict[int, str]:
    """``1:10,20;2:30`` -> ``{1: '10,20', 2: '30'}``，用于 MSTP 实例映射。"""
    out: dict[int, str] = {}
    for chunk in re.split(r"[;；]+", str(spec or "")):
        m = re.match(r"^\s*(\d+)\s*[:：]\s*(.+?)\s*$", chunk)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def compact_vlans(nums, joiner: str = ",", range_sep: str = "-") -> str:
    """把 VLAN 号压成紧凑写法：``[10,11,12,20]`` -> ``10-12,20``。"""
    nums = sorted(set(int(n) for n in nums))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{nums[i]}{range_sep}{nums[j]}")
        else:
            parts.extend(str(n) for n in nums[i:j + 1])
        i = j + 1
    return joiner.join(parts)


# --------------------------------------------------------------------------
# 掩码 / 地址
# --------------------------------------------------------------------------

def prefix_to_dotted(prefix: int) -> str:
    """24 -> 255.255.255.0"""
    prefix = int(prefix)
    if not 0 <= prefix <= 32:
        raise ValueError(f"掩码长度超出范围 0-32：{prefix}")
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


def prefix_to_wildcard(prefix: int) -> str:
    """24 -> 0.0.0.255（反掩码，H3C / 华为 / 思科 ACL 都用它）"""
    prefix = int(prefix)
    if not 0 <= prefix <= 32:
        raise ValueError(f"掩码长度超出范围 0-32：{prefix}")
    bits = (0xFFFFFFFF >> prefix) & 0xFFFFFFFF
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


def dotted_to_prefix(mask: str) -> int:
    """255.255.255.0 -> 24"""
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask.strip()}").prefixlen
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"掩码写法有误：{mask}") from exc


def parse_net(spec: str) -> tuple[str, int]:
    """``192.168.10.0/24`` 或 ``192.168.10.0 255.255.255.0`` -> (网络, 长度)。"""
    spec = (spec or "").strip()
    if "/" in spec:
        net, _, plen = spec.partition("/")
        return net.strip(), int(plen)
    toks = spec.split()
    if len(toks) == 2:
        return toks[0], dotted_to_prefix(toks[1])
    if len(toks) == 1:
        return toks[0], 32
    raise ValueError(f"网段写法有误：{spec}")


def expand_ip_range(spec: str) -> list[str]:
    """``192.168.10.1-192.168.10.20`` 或 ``192.168.10.1-20`` -> 逐个地址。"""
    spec = (spec or "").strip()
    if not spec:
        return []
    if "-" not in spec:
        return [spec]
    left, _, right = spec.partition("-")
    left, right = left.strip(), right.strip()
    start = ipaddress.IPv4Address(left)
    if "." in right:
        end = ipaddress.IPv4Address(right)
    else:
        head = left.rsplit(".", 1)[0]
        end = ipaddress.IPv4Address(f"{head}.{right}")
    if int(end) < int(start):
        start, end = end, start
    return [str(ipaddress.IPv4Address(n)) for n in range(int(start), int(end) + 1)]


def parse_ip_list(spec: str) -> list[str]:
    """逗号分隔的地址或地址范围，展开成地址列表。"""
    out: list[str] = []
    for chunk in split_list(spec):
        out.extend(expand_ip_range(chunk))
    return out


# --------------------------------------------------------------------------
# 结构化解析
# --------------------------------------------------------------------------

@dataclass
class Route:
    net: str
    prefix: int
    nexthop: str
    pref: int = 60
    desc: str = ""


def parse_routes(text: str, default_pref: int = 60, desc: str = "") -> list[Route]:
    """每行：``目标网段/长度 下一跳 [优先级]``。"""
    routes: list[Route] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        toks = line.split()
        if len(toks) < 2:
            raise ValueError(f"路由条目缺少下一跳：{line}")
        dest = toks[0]
        idx = 1
        if "/" in dest:
            net, _, plen = dest.partition("/")
            prefix = int(plen)
        else:
            if len(toks) < 3:
                raise ValueError(f"路由条目缺少掩码或下一跳：{line}")
            net, prefix = dest, dotted_to_prefix(toks[1])
            idx = 2
        nexthop = toks[idx]
        idx += 1
        pref = default_pref
        if idx < len(toks) and toks[idx].lstrip("-").isdigit():
            pref = int(toks[idx])
        routes.append(Route(net, prefix, nexthop, pref, desc))
    return routes


@dataclass
class AclRule:
    action: str      # permit / deny
    proto: str       # ip / tcp / udp / icmp / gre ...
    src: str         # 网段或 any
    dst: str
    op: str = ""     # eq / gt / lt
    port: str = ""


def parse_acl_rules(text: str) -> list[AclRule]:
    """每行：``动作 协议 源 目的 [eq 端口]``。"""
    rules: list[AclRule] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        toks = line.split()
        if len(toks) < 4:
            raise ValueError(f"ACL 规则至少需要 4 段（动作 协议 源 目的）：{line}")
        action, proto, src, dst = toks[0].lower(), toks[1].lower(), toks[2], toks[3]
        if action not in ("permit", "deny"):
            raise ValueError(f"ACL 动作只能是 permit 或 deny：{line}")
        op, port = "", ""
        if len(toks) >= 6 and toks[4].lower() in ("eq", "gt", "lt", "neq"):
            op, port = toks[4].lower(), toks[5]
        rules.append(AclRule(action, proto, src, dst, op, port))
    return rules


def acl_is_advanced(acl_id: str, vendor_key: str = "h3c") -> bool:
    """判断 ACL 编号属于基础 / 标准还是高级 / 扩展。

    两个体系的编号区间完全不同，必须按厂商判断，否则会把思科 100-199 的
    扩展 ACL 误当成标准 ACL，导致生成的规则丢字段。
    """
    digits = re.sub(r"\D", "", acl_id or "")
    if not digits:
        return True  # 命名 ACL 一律按高级 / 扩展处理
    n = int(digits)

    if vendor_key in ("cisco", "ruijie"):
        # 标准 1-99；扩展 100-199；扩张号段 标准 1300-1999、扩展 2000-2699
        if 1300 <= n <= 1999:
            return False
        return n >= 100

    # H3C / 华为：基础 2000-2999，高级 3000-3999
    return n >= 3000


def parse_routes_area(text: str) -> list[tuple[str, int, str]]:
    """OSPF 宣告网段，每行：``192.168.10.0/24 area 0`` 或 ``192.168.10.0 255.255.255.0 0``。

    返回 ``[(网段, 掩码长度, 区域号)]``。
    """
    out: list[tuple[str, int, str]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        low = line.lower()
        if "area" in low:
            idx = low.index("area")
            net_spec = line[:idx].strip()
            area = line[idx + 4:].strip()
        else:
            parts = line.rsplit(None, 1)
            if len(parts) != 2:
                raise ValueError(f"宣告网段缺少区域号：{line}")
            net_spec, area = parts[0].strip(), parts[1].strip()
        if not net_spec or not area:
            raise ValueError(f"宣告网段写法有误：{line}")
        net, plen = parse_net(net_spec)
        out.append((net, plen, area))
    return out


# --------------------------------------------------------------------------
# MAC 地址
# --------------------------------------------------------------------------

def _mac_digits(spec: str) -> str:
    """把任意写法的 MAC 抠成 12 位小写十六进制。"""
    digits = re.sub(r"[^0-9A-Fa-f]", "", str(spec or ""))
    if len(digits) != 12:
        raise ValueError(f"MAC 地址写法有误：{spec}（应为 12 位十六进制）")
    return digits.lower()


def mac_dashed(spec: str) -> str:
    """H3C / 华为写法：0001-0002-0003"""
    d = _mac_digits(spec)
    return "-".join(d[i:i + 4] for i in range(0, 12, 4))


def mac_dotted(spec: str) -> str:
    """思科 / 锐捷写法：0001.0002.0003"""
    d = _mac_digits(spec)
    return ".".join(d[i:i + 4] for i in range(0, 12, 4))


def mac_for(spec: str, vendor_key: str) -> str:
    """按厂商给 MAC 分组符：H3C / 华为用连字符，思科 / 锐捷用点。"""
    return mac_dashed(spec) if vendor_key in ("h3c", "huawei") else mac_dotted(spec)


def parse_macs(spec: str) -> list[str]:
    """逗号 / 空白分隔的 MAC 列表，原样返回（分组符交给 mac_for 处理）。"""
    out: list[str] = []
    for chunk in split_list(spec):
        out.append(_mac_digits(chunk))
    return out


def split_ifaces(spec) -> list[str]:
    """按逗号 / 分号拆接口名。

    不能用空白拆——接口名本身就含空格，例如 ``Vlan-interface 10``、
    ``GigabitEthernet 0/0/1``。用空白拆会把它劈成两半。
    """
    out: list[str] = []
    for chunk in re.split(r"[,，;；]+", str(spec or "")):
        name = chunk.strip()
        if name:
            out.append(name)
    return out


# --------------------------------------------------------------------------
# 时区
# --------------------------------------------------------------------------

def parse_timezone(spec):
    """解析 ``UTC+08:00`` / ``CST-5`` / ``BJ+8`` 这类时区写法。

    返回 ``(名称, 相对 UTC 的分钟偏移)``，东区为正、西区为负；
    解析不出来时返回 ``None``（此时调用方跳过时区配置）。
    """
    text = str(spec or "").strip()
    if not text:
        return None
    m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*([+-])\s*(\d{1,2})(?::?(\d{1,2}))?$", text)
    if not m:
        return None
    name = m.group(1)
    minutes = int(m.group(3)) * 60 + int(m.group(4) or 0)
    if m.group(2) == "-":
        minutes = -minutes
    return name, minutes


def cisco_timezone(zone) -> str:
    """思科 / 锐捷：``clock timezone <名称> <小时> [分钟]``，西区为负数。"""
    name, minutes = zone
    hours, mins = divmod(abs(minutes), 60)
    signed = hours if minutes >= 0 else -hours
    return f"clock timezone {name} {signed} {mins}" if mins else \
        f"clock timezone {name} {signed}"


def h3c_timezone(zone) -> str:
    """H3C / 华为：``clock timezone <名称> {add|minus} <hh:mm:ss>``。"""
    name, minutes = zone
    hours, mins = divmod(abs(minutes), 60)
    sign = "add" if minutes >= 0 else "minus"
    return f"clock timezone {name} {sign} {hours:02d}:{mins:02d}:00"


# --------------------------------------------------------------------------
# 三层接口与环回口命名（四家都不一样，统一在这里转换）
# --------------------------------------------------------------------------

def l3_iface(vendor_key: str, vlan_id) -> str:
    """按厂商拼三层接口名。

    H3C 写 Vlan-interface 10，华为写 Vlanif10，思科写 Vlan10，锐捷写 Vlan 10。
    """
    v = str(vlan_id).strip()
    if vendor_key == "h3c":
        return f"Vlan-interface {v}"
    if vendor_key == "huawei":
        return f"Vlanif{v}"
    if vendor_key == "ruijie":
        return f"Vlan {v}"
    return f"Vlan{v}"


def loopback_iface(vendor_key: str, index=0) -> str:
    """按厂商拼环回接口名。锐捷的接口名与编号之间带空格，华为写 LoopBack。"""
    n = str(index).strip() or "0"
    if vendor_key == "huawei":
        return f"LoopBack{n}"
    if vendor_key == "ruijie":
        return f"Loopback {n}"
    return f"Loopback{n}"


def resolve_ifaces(spec, vendor_key: str) -> list[str]:
    """把接口参数解析成完整接口名列表。

    两种写法都吃：
      · 端口简写    1/0/1-2、XGE1/0/49      → 按厂商展开
      · 完整接口名  Vlan-interface 10       → 原样保留（含空格，不能拆）
    """
    out: list[str] = []
    for token in split_ifaces(spec):
        if re.match(r"^[A-Za-z]", token) and " " in token:
            out.append(token)               # 已经是完整写法，原样用
        else:
            out.extend(ports.expand(token, vendor_key))
    return out


# --------------------------------------------------------------------------
# BGP
# --------------------------------------------------------------------------

@dataclass
class Peer:
    ip: str
    asn: str
    desc: str = ""


def parse_peers(text: str) -> list[Peer]:
    """BGP 邻居列表，每行：``邻居IP 对端AS [描述]``。"""
    peers: list[Peer] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        toks = line.split()
        if len(toks) < 2:
            raise ValueError(f"BGP 邻居需要「IP 对端AS」两段：{line}")
        peers.append(Peer(toks[0], toks[1], " ".join(toks[2:])))
    return peers


def parse_networks(text: str) -> list[tuple[str, int]]:
    """要 network 宣告的网段列表，每行 ``192.168.10.0/24``。"""
    out: list[tuple[str, int]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        out.append(parse_net(line))
    return out


def id_or_name(acl_id: str) -> str:
    return (acl_id or "").strip()


def ensure(value: str, field: str) -> str:
    """必填校验。"""
    if value is None or str(value).strip() == "":
        raise ValueError(f"参数「{field}」不能为空。")
    return str(value).strip()


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "是", "y")
