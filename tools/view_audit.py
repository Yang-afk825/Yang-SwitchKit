"""视图审计：找出「在错误的命令行视图里执行」的命令。

为什么需要这个
--------------
思科 / 锐捷的 `show`、`write`、`copy` 属于特权模式命令，在全局配置模式
（`configure terminal` 之后）执行会直接报 `% Invalid input`。命令本身没错，
错在它被执行时所在的那个视图。

单看代码很难发现：每个块都像模像样，拼起来才暴露出「配置完没退出，
直接 show」。所以写个状态机，把生成的脚本从头到尾走一遍，跟踪当前视图。

    python tools/view_audit.py           # 审计全部场景
    python tools/view_audit.py --full    # 也审计「全部场景合并」后的长脚本

只审计思科和锐捷。H3C / 华为的 `display` 在任意视图都能跑，不存在这个问题。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import registry                    # noqa: E402
from scg.vendors import generate            # noqa: E402

#: 只能在特权模式执行的命令前缀
PRIV_ONLY = ("show ", "write", "copy ", "ping ", "traceroute", "dir",
             "delete ", "reload", "verify ", "clear ")

#: 进入 / 退出全局配置模式
ENTER = ("configure terminal",)
LEAVE = ("end",)


def audit(lines, assume: str = "priv") -> list:
    """走一遍命令流，返回 [(行号, 命令, 当时的视图)]。"""
    state = assume          # priv = 特权模式，config = 全局配置模式
    problems: list = []
    for index, raw in enumerate(lines):
        text = str(raw).strip()
        if not text or text.startswith(("!", "#")):
            continue
        if text in ENTER:
            state = "config"
            continue
        if text in LEAVE:
            state = "priv"
            continue
        # do 前缀是思科专门用来在配置模式里调特权命令的，算合法写法
        if text.startswith("do "):
            continue
        if text.startswith(PRIV_ONLY) and state != "priv":
            problems.append((index, text, state))
    return problems


def build(vendor_key: str, keys) -> list:
    lines: list = []
    for key in keys:
        scenario = registry.get(key)
        params = {p.key: p.default for p in scenario.params}
        result = generate(vendor_key, key, params, scenario)
        for block in result.blocks:
            if block.title:
                lines += ["", f"# ---- {block.title} ----"]
            lines += [str(x) for x in block.lines]
    return lines


def main() -> int:
    full = "--full" in sys.argv
    total = 0
    bad = 0

    for vendor_key in ("cisco", "ruijie"):
        print("=" * 70)
        print(vendor_key)
        print("=" * 70)

        for scenario in registry.SCENARIOS:
            params = {p.key: p.default for p in scenario.params}
            try:
                generate(vendor_key, scenario.key, params, scenario)
            except Exception:      # noqa: BLE001 —— 参数不合法就直接跳过
                continue
            lines = build(vendor_key, (scenario.key,))
            problems = audit(lines)
            total += 1
            if problems:
                bad += 1
                print(f"\n  ✗ {scenario.key}")
                for _, cmd, state in problems[:4]:
                    view = "全局配置模式" if state == "config" else state
                    print(f"      {cmd[:56]:58} ← 在{view}下执行")

        if full:
            keys = tuple(s.key for s in registry.SCENARIOS)
            lines = build(vendor_key, keys)
            problems = audit(lines)
            print(f"\n  合并全部场景：{'✗ ' + str(len(problems)) + ' 处视图错误' if problems else '✓ 没有视图错误'}")
            for _, cmd, state in problems[:6]:
                view = "全局配置模式" if state == "config" else state
                print(f"      {cmd[:56]:58} ← 在{view}下执行")

    print()
    print(f"合计：{total} 个单场景，其中 {bad} 个存在视图错误")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
