"""把 ``registry`` 的场景定义翻译成模型能读的形式。

为什么自动导出而不是手写一份
--------------------------
``registry.SCENARIOS`` 已经是结构化的：每个场景有 key / 名称 / 摘要，
每个参数有 key / 标签 / 类型 / 默认值 / 选项 / 说明 / 示例。这就是一份
现成的 schema。手写第二份必然漂移——改了一边忘了另一边，模型就会
输出程序不认识的字段，用户看到的是一次莫名其妙的失败。

全用 string，不用 integer
------------------------
``window._collect()`` 返回的每个值都是 ``str``，``project.load()`` 也强制
``str(v)``，下游的 ``parse_vlans`` / ``ports.expand`` 全按字符串设计。
schema 里写成 ``integer`` 会让模型吐数字，回填时再转，中间白白多一次
出错机会。

选项值取 value 不取标签
----------------------
``Param.choices`` 是 ``(value, 中文标签)``。给模型看的必须是 value
（``access``），标签（``Access（接终端）``）是界面用的，
由 ``window.choice_maps`` 负责映射。混在一起模型就会把中文标签当值回传。
"""
from __future__ import annotations

from .. import registry
from ..model import Kind, Param, Scenario


def param_schema(param: Param) -> dict:
    """一个参数 → JSON Schema 片段。"""
    if param.kind == Kind.CHOICE:
        values = [str(v) for v, _label in param.choices]
        desc = param.help or ""
        if param.example:
            desc += f"（示例：{param.example}）"
        return {
            "type": "string",
            "enum": values,
            "description": desc.strip(),
        }

    if param.kind == Kind.NUM:
        return {
            "type": "string",
            "pattern": r"^\d+$",
            "description": (param.help or "") + f"（只填数字，示例：{param.example or param.default}）",
        }

    if param.kind == Kind.BOOL:
        return {"type": "string", "enum": ["1", ""], "description": param.help or ""}

    desc = param.help or ""
    if param.example:
        desc += f"（示例：{param.example}）"
    elif param.default:
        desc += f"（默认：{param.default}）"
    return {"type": "string", "description": desc.strip()}


def scenario_schema(scenario: Scenario) -> dict:
    """一个场景 → 完整的 object schema。

    不标 ``required``：``Param`` 没有这个概念，哪些必填只有各厂商
    生成函数自己知道（填错了它抛 ValueError）。与其在这里瞎猜一个
    必填列表误导模型，不如让它把拿不准的放进 ``missing`` 反问用户。
    """
    return {
        "type": "object",
        "properties": {p.key: param_schema(p) for p in scenario.params},
        "additionalProperties": False,
    }


def all_schemas() -> dict:
    """全部场景的 schema，自检脚本与调试用。"""
    return {s.key: scenario_schema(s) for s in registry.SCENARIOS}


# ==========================================================================
# 给模型看的文本形式
# ==========================================================================

def catalog_text() -> str:
    """第一轮：全部场景的一行式清单。约 2.5k token。

    只给 key / 名称 / 摘要 / 参数名。第一轮要做的事只有「选场景」，
    把全部参数说明都塞进来会让成本翻三倍而准确率没有明显提升。
    """
    lines = ["key | 名称 | 用途 | 参数名"]
    for scenario in registry.SCENARIOS:
        keys = ", ".join(p.key for p in scenario.params)
        lines.append(f"{scenario.key} | {scenario.name} | {scenario.summary} | {keys}")
    return "\n".join(lines)


def param_text(scenario: Scenario, current: dict | None = None) -> str:
    """第二轮：选中场景的完整参数说明。约 800 token。"""
    lines = [f"场景：{scenario.name}（{scenario.key}）",
             f"用途：{scenario.summary}"]
    if scenario.versions:
        lines.append(f"适用版本：{scenario.versions}")
    if scenario.notes:
        lines.append("注意事项：" + "；".join(scenario.notes))

    lines.append("")
    lines.append("参数：")
    values = current or {}
    for param in scenario.params:
        kind = {
            Kind.CHOICE: "选项",
            Kind.NUM: "数字",
            Kind.BOOL: "开关",
            Kind.AREA: "多行",
        }.get(param.kind, "文本")

        row = f"  {param.key}  {param.label}  [{kind}]"
        if param.kind == Kind.CHOICE:
            options = " / ".join(str(v) for v, _ in param.choices)
            row += f"  可选：{options}"
        if param.default:
            row += f"  默认：{param.default}"
        if param.example and param.example != param.default:
            row += f"  示例：{param.example}"
        lines.append(row)
        if param.help:
            lines.append(f"      说明：{param.help}")

        filled = str(values.get(param.key, "") or "").strip()
        if filled:
            lines.append(f"      当前已填：{filled}")
    return "\n".join(lines)


def vendor_hint(vendor_key: str) -> str:
    """接口命名习惯，第二轮提示词里带上，能明显减少端口写错的概率。"""
    from ..vendors import get_vendor

    try:
        vendor = get_vendor(vendor_key)
    except ValueError:
        return ""
    return vendor.port_hint or ""
