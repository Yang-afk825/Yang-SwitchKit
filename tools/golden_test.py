"""golden 快照回归测试：锁定 4 厂商 × 29 场景 × 2 变体的生成结果。

为什么需要它
-----------
``tools/smoke_test.py`` 只断言「生成了东西」，不判断生成的是什么。
命令库有 5000 多行，改 h3c.py 里的一个函数，很可能在别的场景、别的厂商上
输出变了却没人发现——这个工具的全部价值建立在「命令是对的」上面，
所以「输出没被意外改动」必须能被自动验证，而不是靠人工逐条看。

它守三件事
----------
1. **输出不变**：每个组合的结果存成快照，逐行比对，不一致就打印差异。
2. **该拦的还在拦**：某些参数组合本来就应该被拒绝（见 ``EXPECTED_FAIL``），
   快照里记下拦截文案；如果哪天校验被删掉、它反而生成出命令了，测试会报警。
3. **详解覆盖率不下降**：README 承诺「每条命令都有详解」，这里逐组核算，
   有命令漏了详解就会列出来。

每个场景跑两个变体
------------------
* ``default`` —— 全部参数取 Registry 里声明的默认值
* ``alt``     —— 布尔项全部取反、下拉项取最后一个选项，覆盖默认值走不到的分支

用法
----
    python tools/golden_test.py              # 比对，有差异返回 1
    python tools/golden_test.py --update     # 重建全部快照（改完命令后执行）
    python tools/golden_test.py --show h3c/lag        # 顺便打印某组输出
    python tools/golden_test.py --update --only h3c   # 只重建某个厂商
    python tools/golden_test.py --no-cover            # 跳过详解覆盖率检查

改完命令库的正确姿势：先跑比对 → 看 diff 确认改动符合预期 → ``--update`` →
把快照文件和代码一起提交。快照的 diff 就是给 reviewer 看的变更说明。
"""
from __future__ import annotations

import difflib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import registry                      # noqa: E402
from scg.explain import coverage              # noqa: E402
from scg.model import Kind                    # noqa: E402
from scg.vendors import VENDORS, generate     # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
TRUTHY = ("1", "true", "yes", "on")
VARIANTS = ("default", "alt")

#: 这些组合本来就该被参数校验拒绝，快照里记的是拦截文案，不是命令。
#:
#: 它们是有意义的断言：把「拒绝非法参数」这件事本身锁住。
#: 如果哪天这里列的组合生成了命令（说明校验被删掉或改坏了），测试会报错。
EXPECTED_FAIL: dict[tuple, str] = {
    ("h3c", "inspect", "alt"): "巡检类全不勾选，应拒绝：至少勾选一类",
    ("huawei", "inspect", "alt"): "巡检类全不勾选，应拒绝：至少勾选一类",
    ("cisco", "inspect", "alt"): "巡检类全不勾选，应拒绝：至少勾选一类",
    ("ruijie", "inspect", "alt"): "巡检类全不勾选，应拒绝：至少勾选一类",
    ("h3c", "gateway", "alt"): "alt 把协议切成 HSRP，H3C 不支持，应拒绝",
    ("huawei", "gateway", "alt"): "alt 把协议切成 HSRP，华为不支持，应拒绝",
}


def _params(scenario, variant: str) -> dict:
    """按变体构造一份参数。"""
    data: dict = {}
    for p in scenario.params:
        if variant == "default":
            data[p.key] = p.default
            continue

        if p.kind == Kind.BOOL:
            on = str(p.default).strip().lower() in TRUTHY
            data[p.key] = "0" if on else "1"
        elif p.kind == Kind.CHOICE and p.choices:
            data[p.key] = p.choices[-1][0]
        else:
            data[p.key] = p.default
    return data


def _generate(vendor_key: str, scenario, variant: str):
    """返回 (快照文本, GenResult 或 None)。异常也落成文本，不往外抛。"""
    try:
        result = generate(vendor_key, scenario.key, _params(scenario, variant), scenario)
    except Exception as exc:  # noqa: BLE001 —— 异常本身就是要锁定的行为
        return f"<<<生成失败：{type(exc).__name__}: {exc}>>>", None

    text = result.to_text(header=True, comments=True)
    if not text.strip():
        return "<<<生成为空>>>", result
    return text, result


def _snapshot_path(vendor_key: str, scenario_key: str, variant: str) -> pathlib.Path:
    return GOLDEN / f"{vendor_key}__{scenario_key}__{variant}.txt"


def _read(path: pathlib.Path) -> str:
    # 统一换行并去掉文件末尾那一个换行，避免 Windows / Linux 与编辑器
    # 的「文件末尾补换行」习惯互相判定为差异
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return text[:-1] if text.endswith("\n") else text


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _diff(expected: str, actual: str) -> str:
    lines = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile="快照（仓库里）", tofile="当前输出", lineterm="", n=2))
    if not lines:
        return "  （仅末尾空行不同）"
    if len(lines) > 40:
        lines = lines[:40] + [f"... 还有 {len(lines) - 40} 行差异未显示"]
    return "\n".join("  " + ln for ln in lines)


def _arg_value(argv: list, flag: str):
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def main() -> int:
    argv = sys.argv[1:]
    update = "--update" in argv
    check_cover = "--no-cover" not in argv
    only = _arg_value(argv, "--only")
    show = _arg_value(argv, "--show")

    vendors = [v for v in VENDORS if only is None or v.key == only]
    if only and not vendors:
        print(f"未知厂商：{only}（可选：{', '.join(v.key for v in VENDORS)}）")
        return 2

    total = 0
    missing: list[str] = []
    changed: list[tuple] = []
    unexpected_fail: list[tuple] = []
    no_longer_fail: list[tuple] = []
    seen: set[pathlib.Path] = set()

    cover_ok = 0
    cover_all = 0
    cover_missed: list[tuple] = []

    for vendor in vendors:
        for scenario in registry.SCENARIOS:
            for variant in VARIANTS:
                total += 1
                path = _snapshot_path(vendor.key, scenario.key, variant)
                seen.add(path)
                actual, result = _generate(vendor.key, scenario, variant)
                failed = result is None

                # ---- 预期失败断言 ----
                why = EXPECTED_FAIL.get((vendor.key, scenario.key, variant))
                label = f"{vendor.key}/{scenario.key}/{variant}"
                if why is not None and not failed:
                    no_longer_fail.append((label, why))
                elif why is None and failed:
                    unexpected_fail.append((label, actual.strip()))

                # ---- 详解覆盖率（只看 default 变体，那是主路径）----
                if check_cover and variant == "default" and result is not None:
                    covered, lines, missed = coverage(vendor.key, result)
                    cover_ok += covered
                    cover_all += lines
                    if missed:
                        cover_missed.append((label, missed))

                if update:
                    _write(path, actual)
                    continue

                if not path.exists():
                    missing.append(label)
                    continue

                expected = _read(path)
                if expected != actual:
                    changed.append((label, expected, actual))

                if show and f"{vendor.key}/{scenario.key}" == show:
                    print("=" * 74)
                    print(f"[{label}]")
                    print(actual)
                    print()

    if update:
        print(f"已写入 {total} 份快照 → {GOLDEN.relative_to(ROOT)}")
        stale = sorted(set(GOLDEN.glob("*.txt")) - seen)
        for path in stale:
            path.unlink()
        if stale:
            print(f"清掉 {len(stale)} 份已无对应场景的旧快照")
        if check_cover:
            print(f"详解覆盖率 {cover_ok}/{cover_all} 行"
                  + ("" if cover_all == cover_ok else "（有未识别命令，见下）"))
            for label, missed in cover_missed:
                print(f"  {label} 未识别 {len(missed)} 行：{missed[:3]}")
        return 0

    print(f"厂商 {len(vendors)} × 场景 {len(registry.SCENARIOS)} × 变体 "
          f"{len(VARIANTS)} = 快照 {total}")

    problems = 0

    if missing:
        problems += len(missing)
        print(f"\n缺少快照 {len(missing)} 份（新加的场景？执行 --update 生成）：")
        for name in missing[:15]:
            print(f"  {name}")
        if len(missing) > 15:
            print(f"  ... 还有 {len(missing) - 15} 份")

    if no_longer_fail:
        problems += len(no_longer_fail)
        print(f"\n本该被拦下、却生成了命令 {len(no_longer_fail)} 组：")
        for name, why in no_longer_fail:
            print(f"  {name} —— {why}")
        print("  说明对应的参数校验可能被删掉或改坏了。")

    if unexpected_fail:
        problems += len(unexpected_fail)
        print(f"\n意外失败 {len(unexpected_fail)} 组：")
        for name, msg in unexpected_fail[:10]:
            print(f"  {name} -> {msg}")
        if len(unexpected_fail) > 10:
            print(f"  ... 还有 {len(unexpected_fail) - 10} 组")

    for name, expected, actual in changed[:10]:
        print(f"\n输出有变化：{name}")
        print(_diff(expected, actual))
    if len(changed) > 10:
        print(f"\n... 还有 {len(changed) - 10} 组输出有变化")
    problems += len(changed)

    if check_cover:
        pct = (cover_ok / cover_all * 100) if cover_all else 100.0
        print(f"\n详解覆盖：{cover_ok} / {cover_all} 行 = {pct:.1f}%")
        if cover_missed:
            problems += 1
            print("以下命令没有对应详解（README 承诺 100%），请在 scg/explain.py 补条目：")
            for label, missed in cover_missed[:10]:
                print(f"  {label}：{len(missed)} 行，例如 {missed[0]!r}")
            if len(cover_missed) > 10:
                print(f"  ... 还有 {len(cover_missed) - 10} 组")

    expected_fail_hits = sum(
        1 for (v, s, var) in EXPECTED_FAIL
        if (only is None or v == only) and any(
            vv.key == v and ss.key == s for vv in vendors for ss in registry.SCENARIOS))
    print(f"预期失败 {expected_fail_hits} 组（锁定参数校验）："
          + ("全部符合预期" if not no_longer_fail else "有组合未被拦下"))

    if problems:
        print("\n结论：未通过。确认改动符合预期后执行 python tools/golden_test.py --update")
        return 1

    print("结论：全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
