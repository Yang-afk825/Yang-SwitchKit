"""把一套参数从一家厂商换算到另一家。

为什么需要这一层
----------------
界面上的「切厂商」只做了一件事：拿着**目标厂商自己那份参数**重新生成命令。
那份参数如果没填过，就是默认值 —— 刚在 H3C 下填的 VLAN 30、端口 1/0/1-8、
网关 192.168.1.254，切到华为之后全部蒸发。所以「一键转换」真正要补的不是
重算链路（已经有了），而是把参数**搬过去**，并且把端口编号**换算成对方认的写法**。

只做两件事
----------
1. 参数搬运：29 个场景的参数 key 四家完全一样，值直接抄。唯一需要动的是端口。
2. 端口换算：按「设备序号等价」原则把首段槽位换成对方的起始值。

为什么敢做端口换算
------------------
四家的接口编号都是「首段 = 设备 / 槽位号」：

    H3C      1/0/1      盒式固定 1/0/x，堆叠成员从 1 起
    华为     0/0/1      盒式固定 0/0/x，堆叠成员从 0 起
    思科     0/1        单机两段，槽位从 0 起
    锐捷     0/1        单机两段，槽位从 0 起

所以「第几台设备的第几个口」这个概念是跨厂商的，只有起始值不同。换算就是
先还原成设备序号，再按对方的起始值写回去：

    H3C 1/0/1  →  序号 0  →  华为 0/0/1        （三段对三段，段内保持）
    H3C 2/0/5  →  序号 1  →  华为 1/0/5        （堆叠第二台）
    H3C 1/0/1  →  序号 0  →  思科 0/1          （三段压两段，丢掉子槽位）
    思科 0/1   →  序号 0  →  H3C 1/0/1         （两段补三段，子槽位补 0）

为什么换算结果仍然必须人工核对
------------------------------
上面这套成立的前提是「源写法本来就是这台设备的真实编号」。有几种情况会偏：

* 思科 Catalyst 9300 堆叠时也写三段 ``1/0/1``，但它的首段从 1 起 —— 和三段
  压两段时按 0 起算的假设不一致，这种要人工判断是哪台。
* 面板上的丝印编号和 CLI 编号不一定一致（尤其是 48 口 + 4 万兆的机型）。
* 换设备不等于换同型号，端口密度可能对不上。

所以换算记录会随脚本一起显示出来（见 ``describe``），每条映射都摆明给你核，
而不是悄悄改完让你自己发现。这也是项目「查不出把握就不假装有把握」的一贯做法。

不换算的东西
------------
* VLAN 号、IP、掩码、ACL 编号、聚合组号、密码 —— 这些是跨厂商的，原样搬。
* 接口前缀（``Ten-GigabitEthernet`` / ``XGE`` / ``GE``）—— 留给 ``ports.py``
  按目标厂商重写，这里只碰数字。
"""
from __future__ import annotations

import re

from .model import Kind

#: 参数名或标签里带这些词的，才当成端口写法处理。
#: 宁可漏转（用户自己改一眼就能看见），也不要把 IP、VLAN、时间误伤成端口。
_PORT_WORDS = ("port", "端口", "接口")

#: 各厂商接口编号的标准形态：(段数, 首段起始值)。
#: 名单外的厂商按「原样不动」处理，不猜。
_LAYOUT = {
    "h3c": (3, 1),
    "huawei": (3, 0),
    "cisco": (2, 0),
    "ruijie": (2, 0),
}

#: 一条端口编号。前后都卡死，避开 ``192.168.1.1/24`` 这种带斜杠的地址：
#: 前面不能是字母数字和点，后面不能接数字和点。
#: 只匹配 1~2 个斜杠，所以日期 ``2026/11/08`` 会被 ``(?<![0-9])`` 之外的判断漏过 ——
#: 但它本来也不会出现在端口参数里。
_PORT = re.compile(
    r"(?<![0-9A-Za-z./])"
    r"(?P<alias>[A-Za-z][A-Za-z\-]*)?"
    r"(?P<num>\d+(?:/\d+){1,2})"
    r"(?![0-9.])"
)


def is_port_param(param) -> bool:
    """这个参数的值是不是接口编号。"""
    text = f"{param.key} {param.label}".lower()
    return any(word in text for word in _PORT_WORDS)


def _shift(num_text: str, src: str, dst: str) -> str:
    """把一条接口编号按目标厂商的槽位习惯换算。

    源厂商不在表里、或者写法看不懂时，原样返回 —— 不猜。
    """
    if src not in _LAYOUT or dst not in _LAYOUT:
        return num_text
    parts = [int(x) for x in num_text.split("/")]
    if not 2 <= len(parts) <= 3:
        return num_text

    src_depth, src_base = _LAYOUT[src]
    dst_depth, dst_base = _LAYOUT[dst]

    if len(parts) == 3:
        slot, sub, port = parts
    else:
        slot, port = parts
        sub = 0

    # 先还原成「第几台设备」，再按目标的起始值写回去
    member = slot - src_base
    slot_out = member + dst_base
    if slot_out < dst_base:
        # 减出负数说明源写法本身不合该厂商习惯（比如把思科三段当两段），不硬凑
        return num_text

    if dst_depth >= 3:
        return f"{slot_out}/{sub}/{port}"
    return f"{slot_out}/{port}"


def convert_value(text: str, src: str, dst: str, report: list) -> str:
    """换算一个值里所有端口编号，改动记进 ``report``。"""

    def repl(match: re.Match) -> str:
        alias = match.group("alias") or ""
        old = match.group("num")
        new = _shift(old, src, dst)
        if new != old:
            report.append((alias + old, alias + new))
        return alias + new

    return _PORT.sub(repl, text)


def convert_params(params: dict, scenario, src: str, dst: str) -> tuple[dict, list]:
    """把一套参数从 ``src`` 搬到 ``dst``。

    返回 ``(新参数, 换算记录)``。``换算记录`` 是 ``[(原写法, 新写法), ...]``，
    按出现顺序、已去重；为空表示这次没有端口需要改。
    """
    out = dict(params)
    report: list = []
    if src == dst:
        return out, report

    for param in scenario.params:
        if param.kind not in (Kind.TEXT, Kind.AREA):
            continue
        if not is_port_param(param):
            continue
        value = params.get(param.key)
        if not isinstance(value, str) or not value.strip():
            continue
        out[param.key] = convert_value(value, src, dst, report)

    seen: set = set()
    uniq: list = []
    for pair in report:
        if pair not in seen:
            seen.add(pair)
            uniq.append(pair)
    return out, uniq


def heading_lines(src_name: str, dst_name: str, report: list) -> list:
    """转换说明块，贴在脚本区最前面。

    没有端口改动时只说一句「参数已原样搬过去」；有改动就逐条列出映射，
    让用户一眼能核 —— 这是「自动换算 + 标注待核」的落点。
    """
    out = [f"本脚本由 {src_name} 的方案换算而来，共 {len(report)} 处端口编号已按 "
           f"{dst_name} 的槽位习惯调整，下发前请逐条核对面板编号。"]
    if report:
        pairs = "、".join(f"{old} → {new}" for old, new in report[:8])
        if len(report) > 8:
            pairs += f" 等 {len(report)} 处"
        out.append(pairs)
    return out
