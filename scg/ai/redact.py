"""发送前脱敏。

为什么要有这一步
---------------
在线查错要把用户粘贴的配置发给第三方 API，而一份典型配置里有
内网地址规划、ACL 策略、开局用的明文密码、设备命名规范。
这些发给模型对诊断没有帮助，漏出去却是实实在在的信息泄露。

设计上的两个选择
---------------
1. **默认开，且看得见。** 顶部固定显示「已脱敏 N 处」，不是偷偷替换。
   用户要能确认自己发出去的东西长什么样。
2. **替换成等长占位符，不删行。** 行号和结构必须保留，
   否则模型报回来的行号会跟原文对不上，诊断结果就没法定位了。
"""
from __future__ import annotations

import re

#: 密码类。这里有个坑：``password cipher Admin@123`` 里的 ``cipher`` 是修饰词，
#: 不是值。如果只写成 ``(password|cipher|secret)\s+(\S+)``，
#: 替换掉的是 keyword 本身，明文密码会被留在原地——等于没脱敏。
#: 所以第二个分组要把修饰词一起吃掉，第三个分组才是真正的值。
#: ``(?!-)`` 是为了绕开 password-control / password-recovery 这类命令名。
_RX_SECRET = re.compile(
    r"(?i)\b(password|secret|cipher)"
    r"(\s+(?:cipher|simple|hash|irreversible-cipher)\s+|\s+)"
    r"(?!-)(\S+)"
)

#: IPv4。故意不区分公网私网——地址规划本身就是敏感信息
_RX_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

#: MAC 三种常见写法
_RX_MAC_DASH = re.compile(r"\b[0-9a-fA-F]{4}[.-][0-9a-fA-F]{4}[.-][0-9a-fA-F]{4}\b")
_RX_MAC_COLON = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")

#: 设备名 / 域名。``sysname`` 是 H3C / 华为，``hostname`` 是思科 / 锐捷
_RX_HOST = re.compile(r"(?im)^(\s*(?:sysname|hostname)\s+)(\S+)")

#: 用户名。配置里通常是 local-user <名字> / username <名字>
_RX_USER = re.compile(r"(?im)^(\s*(?:local-user|username)\s+)(\S+)")


def redact(text: str) -> tuple:
    """返回 ``(脱敏后的文本, 替换处数)``。

    顺序有讲究：先处理密码，再处理地址。反过来的话，
    密码里的数字段会被 IP 正则先吃掉，替换出来的东西对不上。
    """
    if not text:
        return "", 0

    count = 0

    def _sub_secret(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}{m.group(2)}******"

    def _sub_value(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}SW"

    def _sub_user(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}user"

    def _sub_ip(m):
        nonlocal count
        count += 1
        return "x.x.x.x"

    def _sub_mac(m):
        nonlocal count
        count += 1
        return "xxxx.xxxx.xxxx"

    out = _RX_SECRET.sub(_sub_secret, text)
    out = _RX_HOST.sub(_sub_value, out)
    out = _RX_USER.sub(_sub_user, out)
    out = _RX_IPV4.sub(_sub_ip, out)
    out = _RX_MAC_DASH.sub(_sub_mac, out)
    out = _RX_MAC_COLON.sub(_sub_mac, out)
    return out, count
