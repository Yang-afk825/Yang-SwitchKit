"""自然语言 → 场景 + 参数。

两轮，不是一轮
-------------
第一轮只做「这是哪个场景」，第二轮才抽参数。看着麻烦，但省很多钱：

* 一轮全塞：29 个场景的全部参数说明，约 6-8k token，每问一句都烧一遍
* 两轮：第一轮 2.5k（只要场景名 + 参数名），第二轮 800（只要选中场景的详细说明）

总 token 反而更少，而且第一轮的判断准确率明显更高——选项少，模型不容易
把「链路聚合」和「端口隔离」搞混。

只输出参数，不输出命令
---------------------
这是整个功能的前提。模型给的是 ``{"vlans": "10,20,30"}`` 这种字段值，
命令仍由 ``registry`` + ``vendors`` 按模板拼。参数是有限的、可枚举的、
可校验的；命令不是。让模型碰命令，等于把 116 组核对过的模板换成它的记忆。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import registry
from ..model import Kind
from . import schema
from .provider import AIError, Provider, extract_json

#: 低于这个置信度就不自动回填，改成让用户在候选里选。
#: 宁可多一步，也不要填错一屏参数。
CONFIDENCE_FLOOR = 0.6

_ROUTE_SYSTEM = """你是一个交换机配置助手的意图识别模块。

用户会用一句中文描述他想在交换机上做什么。你要判断这属于下面哪个场景。

可用的场景（key | 名称 | 用途 | 参数名）：
{catalog}

规则：
1. 只能从上面列表里选一个 key，不要自创。
2. confidence 是你对这个判断的把握，0 到 1 之间的小数。
3. 如果用户的话同时涉及多个场景，选最核心的那一个。
4. 如果完全无法归类，scenario 填空字符串，confidence 填 0。
5. 只输出 JSON，不要解释。

输出格式：{{"scenario": "场景key", "confidence": 0.86}}"""

_EXTRACT_SYSTEM = """你是一个交换机配置助手的参数抽取模块。

用户会用一句中文描述他的配置需求，你要把它翻译成该场景的参数值。

规则：
1. 只输出上面列出的参数名，不要自创字段。
2. 所有值都写成字符串。端口、VLAN、IP 都保持他原话里的写法。
3. 用户没提到、你也无法合理推断的参数，不要填，放进 missing 数组。
4. 拿不准的参数不要猜默认值——填进 missing，用 question 问一句。
5. note 里可以写一句给用户看的补充说明，没有就留空。
6. 只输出 JSON，不要解释。

输出格式：
{"params": {"参数名": "值"}, "missing": ["参数名"], "question": "要问用户的一句话", "note": "补充说明"}"""


@dataclass
class IntentResult:
    """一次解析的结果。"""

    scenario: str = ""
    confidence: float = 0.0
    params: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    question: str = ""
    note: str = ""
    raw: str = ""
    error: str = ""        # 失败原因，界面直接显示
    dropped: list = field(default_factory=list)   # 被丢弃的非法值，界面要提示

    @property
    def ok(self) -> bool:
        return bool(self.scenario) and not self.error

    def scenario_obj(self):
        return registry.get(self.scenario) if self.scenario else None

    def needs_confirm(self) -> bool:
        """置信度不够，或者还有没确定的参数。"""
        return self.confidence < CONFIDENCE_FLOOR or bool(self.missing)


def resolve(text: str, vendor_key: str, cfg, current: dict | None = None,
            on_chunk=None) -> IntentResult:
    """把一句话解析成场景和参数。不抛异常，失败写进 ``error``。"""
    text = (text or "").strip()
    if not text:
        return IntentResult(error="先说一句要做什么")

    provider = Provider(cfg, on_chunk=None)

    try:
        routed = _route(provider, text)
    except AIError as exc:
        return IntentResult(error=exc.user_message())

    scenario_key = routed.get("scenario") or ""
    if not scenario_key:
        return IntentResult(
            error="没听懂要做什么。可以说得具体一点，比如「给 VLAN 10 划 8 个 access 口」",
            raw=routed.get("_raw", ""),
        )

    try:
        scenario = registry.get(scenario_key)
    except Exception:  # noqa: BLE001
        return IntentResult(error=f"模型给了一个不存在的场景：{scenario_key}")

    result = IntentResult(
        scenario=scenario_key,
        confidence=_to_confidence(routed.get("confidence")),
    )

    # 第二轮才开流式：第一轮的输出没什么可看的，第二轮的字对用户有意义
    provider.on_chunk = on_chunk
    try:
        detail = _extract(provider, text, scenario, vendor_key, current)
    except AIError as exc:
        result.error = exc.user_message()
        return result

    clean, dropped = _clean_params(scenario, detail.get("params"))
    result.params = clean
    result.dropped = dropped
    result.missing = [k for k in _as_list(detail.get("missing")) if k in scenario.param_map()]
    result.question = str(detail.get("question") or "").strip()
    result.note = str(detail.get("note") or "").strip()
    result.raw = detail.get("_raw", "")

    # 模型说缺，但实际上给了值 —— 以实际值为准
    result.missing = [k for k in result.missing if not result.params.get(k)]
    return result


# ==========================================================================
# 两轮
# ==========================================================================

def _route(provider: Provider, text: str) -> dict:
    messages = [
        {"role": "system", "content": _ROUTE_SYSTEM.format(catalog=schema.catalog_text())},
        {"role": "user", "content": text},
    ]
    reply = provider.chat(messages, want_json=True)
    data = extract_json(reply)
    if not isinstance(data, dict):
        raise AIError("bad_format", "意图识别没有返回对象")
    data["_raw"] = reply
    return data


def _extract(provider: Provider, text: str, scenario, vendor_key: str,
             current: dict | None) -> dict:
    hint = schema.vendor_hint(vendor_key)
    system = _EXTRACT_SYSTEM + "\n\n参数说明：\n" + schema.param_text(scenario, current)
    if hint:
        system += f"\n\n该厂商的接口命名习惯：{hint}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    reply = provider.chat(messages, want_json=True)
    data = extract_json(reply)
    if isinstance(data, list):
        # 有的模型会把参数直接吐成一个列表，包一层救回来
        data = {"params": {}, "missing": [], "_raw": reply}
    if not isinstance(data, dict):
        raise AIError("bad_format", "参数抽取没有返回对象")
    data["_raw"] = reply
    return data


# ==========================================================================
# 清洗
# ==========================================================================

def _clean_params(scenario, raw) -> tuple:
    """只留下这个场景认得的参数，非法值丢弃并记录。

    三层过滤：key 合法性、类型、选项范围。宁可少填一个参数让用户自己补，
    也不要把一个错值塞进表单——错值会一路走到生成的命令里。
    """
    clean: dict = {}
    dropped: list = []
    if not isinstance(raw, dict):
        return clean, dropped

    allowed = scenario.param_map()
    rx_num = re.compile(r"^-?\d+$")

    for key, value in raw.items():
        if key not in allowed:
            dropped.append(f"{key}（不是 {scenario.key} 的参数）")
            continue

        param = allowed[key]
        text = "" if value is None else str(value).strip()
        if not text:
            continue

        if param.kind == Kind.CHOICE:
            values = [str(v) for v, _ in param.choices]
            if text not in values:
                dropped.append(f"{key}={text}（不在可选项里：{'/'.join(values)}）")
                continue
        elif param.kind == Kind.NUM:
            if not rx_num.match(text):
                dropped.append(f"{key}={text}（应该只填数字）")
                continue
        elif param.kind == Kind.BOOL:
            text = "1" if text.lower() in ("1", "true", "yes", "on", "是", "开") else ""

        clean[key] = text

    return clean, dropped


def _to_confidence(value) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, num))


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def describe(result: IntentResult) -> str:
    """给界面用的一句话摘要。"""
    if result.error:
        return result.error
    scenario = result.scenario_obj()
    name = scenario.name if scenario else result.scenario
    return f"{name}（把握 {int(result.confidence * 100)}%）"
