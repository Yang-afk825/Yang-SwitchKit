"""在线语义补充：接在配置体检和语法校对之后。

为什么是第三层
-------------
「配置体检」页现在有两层检查，都能离线跑：

1. ``audit.py``  查**安全**——SNMP 团体名、VLAN1 管理、NTP、空闲端口，
   视角是等保测评和项目验收。
2. ``rules.py``  查**语法与结构**——命令跑没跑在对的视图、基础 ACL 是不是
   带了协议字段、端口编号符不符合厂商惯例、关键字有没有拼错。

这两层都是**单点判定**：看某一行、某一段对不对。剩下那一类问题需要读懂
整段配置的意图才能发现：

* 两段配置互相冲突（VLAN 放通了，PVID 又改回 1）
* 缺配套命令（配了 DHCP 池，接口上没 ``dhcp select``）
* 策略顺序错了（ACL 的 deny 写在 permit 前面，后面全废）

这才是该交给模型的部分。

必须带上已有结论
---------------
prompt 里要列出前两层已经报出的问题。不然模型会把「没开 SSH」这种事
换个说法再讲一遍，看起来像发现了新东西，实际上是同一件事说了两次。
更糟的是它可能推翻前两层的判断——那说明它连基础语法都没看懂。
"""
from __future__ import annotations

from . import redact as redactor
from . import rules
from .provider import AIError, Provider, extract_json

_SYSTEM = """你是一名网络设备配置审核专家，熟悉 H3C Comware、华为 VRP、思科 IOS、锐捷 RGOS。

用户会给你一段交换机配置，以及本地两层检查（安全体检、语法校对）已经发现的问题。
你的任务：只补充那两层覆盖不到的问题，也就是需要读懂整段配置意图才能发现的：

- 配置之间的冲突（VLAN 放通与 PVID 矛盾、地址重叠、路由互相覆盖）
- 缺失的配套命令（配了 DHCP 池但接口没启用、配了 ACL 但没应用）
- 策略顺序问题（deny 在 permit 之前导致后续规则失效）
- 逻辑上跑不通的配置组合

严格约束：
1. 不要重复本地已经报出的问题，一个字都不要。
2. 不要重写或"优化"用户的配置，只指出问题。
3. 每条结论必须有依据：指向具体行，或指出缺少哪条命令。没有依据的不要写。
4. 拿不准就不要报。宁可少说，不要说"可能"。
5. 最多报 8 条，按严重程度排序。
6. 只输出 JSON，不要任何解释性文字。

输出格式：
{"findings": [{"line": 行号或0, "level": "high|mid|low", "message": "问题是什么", "evidence": "依据", "fix": "怎么改"}], "note": "一句话总结"}

没有发现新问题时输出 {"findings": [], "note": "没有发现前两层之外的配置问题"}。"""

#: 模型给的级别 → 界面用的级别。体检页的 tag 是 high/mid/low 那套
_LEVELS = {"high", "mid", "low"}


def review(text: str, vendor_key: str, known: list, cfg, on_chunk=None) -> dict:
    """跑一次在线语义补充。不抛异常，失败写进 ``error``。

    返回 ``{"findings": [...], "note": str, "error": str, "redacted": int}``。
    """
    result = {"findings": [], "note": "", "error": "", "redacted": 0}

    source = text or ""
    if not source.strip():
        result["error"] = "没有内容可分析"
        return result

    payload = source
    if cfg.redact:
        payload, count = redactor.redact(payload)
        result["redacted"] = count

    try:
        provider = Provider(cfg, on_chunk=on_chunk)
        reply = provider.chat([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_prompt(vendor_key, payload, known)},
        ], want_json=True)
    except AIError as exc:
        result["error"] = exc.user_message()
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"在线分析失败：{exc}"
        return result

    try:
        data = extract_json(reply)
    except AIError:
        # 模型没按格式回，但它说的话可能仍然有价值，原样带回去
        result["note"] = reply.strip()[:800]
        return result

    if not isinstance(data, dict):
        result["note"] = str(data)[:800]
        return result

    result["note"] = str(data.get("note") or data.get("summary") or "").strip()
    items = data.get("findings")
    if isinstance(items, list):
        result["findings"] = clean_findings(items, len(source.splitlines()))
    return result


def clean_findings(items, total_lines: int) -> list:
    """把模型给的 findings 洗一遍：扔掉空条目、校正级别、夹紧行号。

    单独抽出来是为了能测。``tools/ai_test.py`` 里有一组用例专门喂它
    越界行号、非法级别、空 message，这些在真实响应里都出现过。
    """
    out: list = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue

        level = str(item.get("level") or "low").lower()
        if level not in _LEVELS:
            level = "low"

        try:
            line_no = int(item.get("line") or 0)
        except (TypeError, ValueError):
            line_no = 0
        if line_no < 0 or line_no > total_lines:
            line_no = 0

        out.append({
            "line": line_no,
            "level": level,
            "message": message,
            "evidence": str(item.get("evidence") or "").strip(),
            "fix": str(item.get("fix") or "").strip(),
        })
    return out[:8]


def _build_prompt(vendor_key: str, payload: str, known: list) -> str:
    from ..vendors import get_vendor

    try:
        vendor_name = get_vendor(vendor_key).name
    except ValueError:
        vendor_name = vendor_key or "未知"

    if known:
        already = "\n".join(f"- {item}" for item in known)
    else:
        already = "- 本地检查没有发现问题"

    return (
        f"设备厂商：{vendor_name}\n\n"
        f"本地检查已经报出的问题（不要重复）：\n{already}\n\n"
        f"待审核的配置：\n```\n{payload}\n```\n\n"
        "请只补充上面没有覆盖到的问题。"
    )


def known_titles(report, syntax_findings) -> list:
    """把前两层的结论压成一句话列表，喂给模型避免重复。"""
    out: list = []
    if report is not None:
        for finding in getattr(report, "findings", ()):
            title = getattr(finding, "title", "") or getattr(finding, "message", "")
            if title:
                out.append(str(title))
    for finding in syntax_findings or ():
        if isinstance(finding, rules.Finding):
            out.append(finding.message)
        else:
            out.append(str(finding))
    return out[:40]
