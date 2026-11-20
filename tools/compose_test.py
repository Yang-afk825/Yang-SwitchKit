"""合并整理测试：多个场景拼成一份配置时，冗余有没有去干净。

    python tools/compose_test.py

不需要图形环境。测的是 scg/compose.py 的两个函数：
``tidy_commands``（去重复的进视图/保存）和 ``dedup_lines``（提示去重）。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import compose, registry            # noqa: E402
from scg.vendors import VENDORS, generate     # noqa: E402

FAILS: list[str] = []

#: 用这三个场景做样本：一个二层、一个聚合、一个三层，结构差异够大
SAMPLE = ("vlan_port", "lag", "ospf")


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}" + (f"　{detail}" if detail else ""))
        FAILS.append(name)


def build(vendor_key: str, keys) -> list:
    """按界面的方式把多个场景拼成一份命令行列表。"""
    lines: list = []
    for key in keys:
        scenario = registry.get(key)
        params = {p.key: p.default for p in scenario.params}
        result = generate(vendor_key, key, params, scenario)
        for block in result.blocks:
            if block.title:
                lines.append("")
                lines.append(f"# ---- {block.title} ----")
            lines.extend(block.lines)
        for note in result.notes:
            lines.append(f"# [提示] {note}")
    return lines


def count(lines, command: str) -> int:
    return sum(1 for line in lines if line.strip() == command)


def main() -> int:
    print("一、四厂商：三个场景合并后只剩一次进入视图、一次保存")

    for vendor in VENDORS:
        enter, save = compose.vendor_cmds(vendor.key)
        raw = build(vendor.key, SAMPLE)
        tidy = compose.tidy_commands(compose.dedup_lines(raw), vendor.key)

        check(f"{vendor.name} 能取到进出视图命令", bool(enter and save),
              f"enter={enter} save={save}")

        for cmd in enter:
            # H3C / 华为 能安全删掉重复的进入视图，思科 / 锐捷 必须保留
            expected = 1 if vendor.key in compose.ENTER_SAFE_TO_SLIM else count(raw, cmd)
            check(f"{vendor.name}「{cmd}」剩 {expected} 次",
                  count(tidy, cmd) == expected, f"实际 {count(tidy, cmd)} 次")
        # 只对最后那条（真正写盘的命令）断言只剩一次。
        # 前面那些是退出命令（return / end），思科和锐捷的核对块里本来就
        # 还要用 end 退出配置模式，不能一并算进来
        for cmd in save[-1:]:
            check(f"{vendor.name}「{cmd}」只剩 1 次",
                  count(tidy, cmd) == 1, f"实际 {count(tidy, cmd)} 次")

        check(f"{vendor.name} 整理后比原来短", len(tidy) < len(raw),
              f"{len(raw)} → {len(tidy)}")

    print("\n二、只生成一个场景时，输出不该被改动")

    for vendor in VENDORS:
        raw = build(vendor.key, ("vlan_port",))
        tidy = compose.tidy_commands(compose.dedup_lines(raw), vendor.key)
        check(f"{vendor.name} 单场景原样输出", raw == tidy,
              f"{len(raw)} → {len(tidy)} 行")

    print("\n三、真实命令不能被误删")

    # 各家的写法本来就不一样，按实际命令核对
    expect = {
        "h3c": ("port access vlan 10", "interface Bridge-Aggregation 1"),
        "huawei": ("port default vlan 10", "interface Eth-Trunk 1"),
        "cisco": ("switchport access vlan 10", "interface Port-channel1"),
        "ruijie": ("switchport access vlan 10", "interface AggregatePort 1"),
    }
    for vendor in VENDORS:
        raw = build(vendor.key, SAMPLE)
        tidy = compose.tidy_commands(compose.dedup_lines(raw), vendor.key)
        joined = "\n".join(tidy)
        for needle in ("vlan 10",) + expect[vendor.key]:
            check(f"{vendor.name} 保留了「{needle}」", needle in joined)

        # 思科 / 锐捷：保留的 end 和 configure terminal 必须成对，
        # 否则后面的配置命令会落在错误视图里
        if vendor.key not in compose.ENTER_SAFE_TO_SLIM:
            enters = sum(1 for line in tidy if line.strip() == "configure terminal")
            ends = sum(1 for line in tidy if line.strip() == "end")
            check(f"{vendor.name} 进入配置模式与退出的数量配得上",
                  enters >= ends, f"configure terminal {enters} 次，end {ends} 次")

    print("\n四、提示去重")

    dup = ["# [提示] Hello / Dead 定时器要一致",
           "# [提示] Hello/Dead 定时器要一致",
           "# [提示] 完全不同的另一条提示"]
    got = compose.dedup_lines(dup)
    check("只差空格斜杠的两条合并成一条", len(got) == 2, f"剩 {len(got)} 条")
    check("不相干的提示保留", any("完全不同的" in line for line in got))

    print("\n五、边界情况")

    check("空列表不炸", compose.tidy_commands([], "h3c") == [])
    check("未知厂商原样返回",
          compose.tidy_commands(["system-view", "x"], "不存在的厂商")
          == ["system-view", "x"])
    check("没有保存命令时不动",
          compose.tidy_commands(["system-view", "vlan 10"], "h3c")
          == ["system-view", "vlan 10"])

    print()
    if FAILS:
        print(f"结论：{len(FAILS)} 项未通过")
        return 1
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
