"""端口范围解析与展开。

把运维习惯的简写（``1/0/1-8``、``1/0/1,1/0/3``、``XGE1/0/49``）展开成
各厂商的完整接口名，避免手写几十条接口命令时出错。
"""
from __future__ import annotations

import re

# 接口类型别名 -> 内部标识
_ALIAS = {
    "g": "gigabit",
    "ge": "gigabit",
    "gi": "gigabit",
    "gigabitethernet": "gigabit",
    "xge": "ten-gigabit",
    "te": "ten-gigabit",
    "tengige": "ten-gigabit",
    "tengigabitethernet": "ten-gigabit",
    "fge": "forty-gigabit",
    "fo": "forty-gigabit",
    "fortygige": "forty-gigabit",
    "fortygigabitethernet": "forty-gigabit",
    "hge": "hundred-gigabit",
    "hu": "hundred-gigabit",
    "hundredgige": "hundred-gigabit",
    "hundredgigabitethernet": "hundred-gigabit",
    "mge": "m-gigabit",
    "mgigabitethernet": "m-gigabit",
}

# 各厂商对同一接口类型的实际写法
_VENDOR_PORT_NAME = {
    "h3c": {
        "gigabit": "GigabitEthernet",
        "ten-gigabit": "Ten-GigabitEthernet",
        "forty-gigabit": "FortyGigE",
        "hundred-gigabit": "HundredGigE",
        "m-gigabit": "M-GigabitEthernet",
    },
    "huawei": {
        "gigabit": "GigabitEthernet",
        "ten-gigabit": "XGigabitEthernet",
        "forty-gigabit": "40GE",
        "hundred-gigabit": "100GE",
        "m-gigabit": "MEth",
    },
    "cisco": {
        "gigabit": "GigabitEthernet",
        "ten-gigabit": "TenGigabitEthernet",
        "forty-gigabit": "FortyGigabitEthernet",
        "hundred-gigabit": "HundredGigabitEthernet",
        "m-gigabit": "GigabitEthernet",
    },
    "ruijie": {
        "gigabit": "GigabitEthernet",
        "ten-gigabit": "TenGigabitEthernet",
        "forty-gigabit": "FortyGigabitEthernet",
        "hundred-gigabit": "HundredGigabitEthernet",
        "m-gigabit": "GigabitEthernet",
    },
}

# 锐捷的接口名与编号之间带空格
_VENDOR_SEP = {"ruijie": " "}

_SPLIT = re.compile(r"[,，;；\s]+")
_PREFIXED = re.compile(r"^([A-Za-z][A-Za-z\-]*)\s*(.+)$")


class PortError(ValueError):
    """端口写法无法解析。"""


def port_name(vendor: str, alias: str, number: str) -> str:
    """拼出厂商实际使用的接口名。"""
    table = _VENDOR_PORT_NAME.get(vendor, _VENDOR_PORT_NAME["h3c"])
    base = table.get(alias, "GigabitEthernet")
    return f"{base}{_VENDOR_SEP.get(vendor, '')}{number}"


def _split_number(text: str) -> tuple[str, int]:
    """把 ``1/0/12`` 拆成前缀 ``1/0/`` 与数字 12。"""
    text = text.strip()
    if "/" in text:
        head, last = text.rsplit("/", 1)
        head += "/"
    else:
        head, last = "", text
    if not last.isdigit():
        raise PortError(f"无法识别的端口编号：{text}")
    return head, int(last)


def expand(spec: str, vendor: str = "h3c") -> list[str]:
    """展开端口写法，返回去重后的完整接口名列表。

    支持：``1/0/1-8``、``1/0/1-1/0/8``、``1/0/1,1/0/3``、``XGE1/0/49``。
    """
    spec = (spec or "").strip()
    if not spec:
        return []

    result: list[str] = []
    for chunk in _SPLIT.split(spec):
        chunk = chunk.strip()
        if chunk:
            result.extend(_expand_one(chunk, vendor))

    seen: set[str] = set()
    uniq: list[str] = []
    for item in result:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _expand_one(chunk: str, vendor: str) -> list[str]:
    alias = "gigabit"
    body = chunk

    m = _PREFIXED.match(chunk)
    if m:
        key = m.group(1).lower().replace("-", "")
        if key in _ALIAS:
            alias = _ALIAS[key]
            body = m.group(2).strip()

    if "-" in body:
        left, right = body.split("-", 1)
        head, start = _split_number(left.strip())
        right = right.strip()
        if "/" in right:
            _, end = _split_number(right)
        elif right.isdigit():
            end = int(right)
        else:
            raise PortError(f"无法识别的端口范围：{chunk}")
        if end < start:
            start, end = end, start
        if end - start > 512:
            raise PortError(f"端口范围过大（{end - start + 1} 个）：{chunk}")
        return [port_name(vendor, alias, f"{head}{n}") for n in range(start, end + 1)]

    head, num = _split_number(body)
    return [port_name(vendor, alias, f"{head}{num}")]


def expand_lines(ports: list[str], per_line: int = 4, indent: str = "") -> list[str]:
    """把接口名列表按每行若干个排版，减少命令行数、便于阅读。"""
    if not ports:
        return []
    out: list[str] = []
    for i in range(0, len(ports), per_line):
        out.append(indent + " ".join(ports[i:i + per_line]))
    return out


_TAIL = re.compile(r"^(.*?)(\d+)$")


def sequential(ports: list[str]) -> tuple[bool, str, str]:
    """判断端口是否首尾相连（可合并成 range 写法）。

    返回 ``(是否连续, 首个, 末个)``；连续时调用方可合并为
    ``interface range 首个 to 末个``，避免几十行重复配置。
    """
    if len(ports) < 2:
        return False, "", ""
    heads, tails = [], []
    for name in ports:
        m = _TAIL.match(name)
        if not m:
            return False, "", ""
        heads.append(m.group(1))
        tails.append(int(m.group(2)))
    if len(set(heads)) != 1:
        return False, "", ""
    if tails != list(range(tails[0], tails[0] + len(tails))):
        return False, "", ""
    return True, ports[0], ports[-1]
