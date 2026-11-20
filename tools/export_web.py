"""把桌面版的命令库导出成单文件网页。

设计上**不重写任何命令生成逻辑** —— 网页版拿到的全部是桌面版已经渲染好的
文本，和 exe 里的内容逐字一致。这样命令库永远只有一份来源，改完桌面版重新
跑一次本脚本就行，不会出现「网页写得对、exe 写得错」或者反过来。

产出：``docs/web/Yang-SwitchKit-Web.html``，单文件、无外部依赖、双击可用，
手机上也能打开。

用法：python tools/export_web.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import diagnose, explain, registry          # noqa: E402
from scg.model import Kind                          # noqa: E402
from scg.vendors import VENDORS, generate           # noqa: E402

OUT = ROOT / "docs" / "web"
VERSION = "V3.0.0"


def _samples(scenario) -> list:
    """每个场景导出两组示例：默认参数 + 换一个主要选项。"""
    base = {p.key: p.default for p in scenario.params}
    out = [("默认参数", dict(base))]

    for param in scenario.params:
        if param.kind != Kind.CHOICE or len(param.choices) < 2:
            continue
        for value, label in param.choices:
            if str(value) == str(base.get(param.key)):
                continue
            alt = dict(base)
            alt[param.key] = value
            out.append((label, alt))
            break
        if len(out) >= 2:
            break
    return out


def _display(param, value: str) -> str:
    """下拉选项在网页上显示中文标签，别把 access / trunk 这种裸值直接丢给用户。"""
    if param.kind == Kind.CHOICE:
        for raw, label in param.choices:
            if str(raw) == str(value):
                return label
    if param.kind == Kind.BOOL:
        return "启用" if str(value).strip() in ("1", "true", "yes", "on", "是") else "不启用"
    return str(value or "")


def _commands(scenario, params) -> dict:
    result = {}
    for vendor in VENDORS:
        try:
            gen = generate(vendor.key, scenario.key, params, scenario)
            result[vendor.key] = gen.to_text(header=False)
        except ValueError as exc:
            result[vendor.key] = "# 该厂商不支持本场景\n# " + str(exc)
        except Exception as exc:  # noqa: BLE001
            result[vendor.key] = "# 生成失败\n# " + str(exc)
    return result


def build() -> dict:
    data = {
        "name": "Yang-SwitchKit",
        "version": VERSION,
        "vendors": [
            {"key": v.key, "name": v.name, "platform": v.platform,
             "port_hint": v.port_hint}
            for v in VENDORS
        ],
        "scenarios": [],
        "manuals": {},
        "compare": explain.render_compare(),
        "diagnose": [],
    }

    for scenario in registry.SCENARIOS:
        item = {
            "key": scenario.key,
            "name": scenario.name,
            "group": scenario.group,
            "summary": scenario.summary,
            "versions": scenario.versions,
            "notes": list(scenario.notes),
            "params": [
                {"label": p.label, "help": p.help, "example": p.example,
                 "default": str(p.default or "")}
                for p in scenario.params
            ],
            "samples": [],
        }
        for label, params in _samples(scenario):
            item["samples"].append({
                "label": label,
                # 用参数的中文标签当 key，网页端直接照着参数表渲染，不用再对一遍
                "values": {p.label: _display(p, params.get(p.key, ""))
                           for p in scenario.params},
                "commands": _commands(scenario, params),
            })
        data["scenarios"].append(item)

    for vendor in VENDORS:
        data["manuals"][vendor.key] = explain.render_manual(vendor.key)

    for case in diagnose.CASES:
        data["diagnose"].append({
            "key": case.key,
            "name": case.name,
            "symptom": case.symptom,
            "steps": {
                "h3c": diagnose.render(case, "h3c", include_header=False),
                "cisco": diagnose.render(case, "cisco", include_header=False),
            },
        })

    return data


def main() -> int:
    data = build()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 内嵌到 <script> 里，必须把 </ 转义掉，否则命令里出现 </script> 会把页面截断
    payload = payload.replace("</", "<\\/")

    template = (pathlib.Path(__file__).parent / "web_template.html").read_text(
        encoding="utf-8")
    html = template.replace("/*__DATA__*/", payload).replace("__VERSION__", VERSION)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "Yang-SwitchKit-Web.html"
    target.write_text(html, encoding="utf-8")

    size = target.stat().st_size / 1024
    print(f"场景 {len(data['scenarios'])} 个")
    print(f"示例 {sum(len(s['samples']) for s in data['scenarios'])} 组")
    print(f"输出 {target}  {size:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
