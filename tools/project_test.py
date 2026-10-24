"""方案文件读写测试：往返一致 + 各种坏文件怎么拒。

不需要启动 Tkinter，跑起来是毫秒级的：

    python tools/project_test.py

测两件事：

1. **往返一致** —— 存进去什么，读出来还是什么
2. **坏输入不崩** —— 文件损坏、选错文件、版本太新，要么明确拒绝，
   要么丢掉坏的那一条继续读，不能整个炸掉或者静默读出半份方案
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scg import project, registry          # noqa: E402
from scg.vendors import VENDORS            # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}" + (f"　{detail}" if detail else ""))
        FAILS.append(name)


def expect_error(name: str, fn, expect_in: str = "") -> None:
    try:
        fn()
    except project.ProjectError as exc:
        if expect_in and expect_in not in str(exc):
            check(name, False, f"报错文案里没有出现「{expect_in}」：{exc}")
        else:
            check(name, True)
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"抛的不是 ProjectError 而是 {type(exc).__name__}: {exc}")
    else:
        check(name, False, "本该报错却读进去了")


def main() -> int:
    print("一、往返一致")

    original = project.Project(
        vendor="huawei",
        params={
            "huawei": {
                "vlan_port": {"vlans": "10,20,30", "vlan_names": "10:销售,20:研发",
                              "port_type": "trunk", "ports": "0/0/1-8", "pvid": "99"},
                "ospf": {"area": "0", "networks": "192.168.1.0/24"},
            },
            "h3c": {
                "lag": {"group": "1", "mode": "lacp", "ports": "1/0/1-2"},
            },
        },
        order=["vlan_port", "lag", "ospf"],
        comments=False,
        theme="dark",
    )

    text = project.dump(original)
    loaded, warnings = project.load(text)

    check("厂商还原", loaded.vendor == "huawei", loaded.vendor)
    check("注释开关还原", loaded.comments is False)
    check("主题还原", loaded.theme == "dark")
    check("已生成顺序还原", loaded.order == ["vlan_port", "lag", "ospf"], str(loaded.order))
    check("场景参数逐字一致",
          loaded.params["huawei"]["vlan_port"] == original.params["huawei"]["vlan_port"])
    check("另一厂商的参数也在", "h3c" in loaded.params and "lag" in loaded.params["h3c"])
    check("往返不产生警告", warnings == [], str(warnings))
    check("场景计数正确", loaded.count() == 3, str(loaded.count()))

    # 再存一次，文本应当完全一致（除了 saved_at 时间戳）
    def strip_time(s: str) -> str:
        return "\n".join(ln for ln in s.splitlines() if '"saved_at"' not in ln)

    check("二次序列化稳定", strip_time(text) == strip_time(project.dump(loaded)))

    print("\n二、值类型清洗")

    weird = project.load('{"format":"yang-switchkit-project","format_version":1,'
                         '"vendor":"h3c","params":{"h3c":{"vlan_port":{"vlans":123,'
                         '"pvid":null}}}}')[0]
    check("数字转成字符串",
          weird.params["h3c"]["vlan_port"]["vlans"] == "123",
          repr(weird.params["h3c"]["vlan_port"].get("vlans")))
    check("null 转成空串",
          weird.params["h3c"]["vlan_port"]["pvid"] == "",
          repr(weird.params["h3c"]["vlan_port"].get("pvid")))

    print("\n三、坏输入")

    expect_error("空文件被拒", lambda: project.load("   "), "空的")
    expect_error("不是 JSON 被拒", lambda: project.load("system-view\nvlan 10"), "JSON")
    expect_error("别的 JSON 被拒", lambda: project.load('{"hello": 1}'), "方案文件")
    expect_error("数组被拒", lambda: project.load("[1,2,3]"), "方案对象")
    expect_error(
        "未来版本被拒",
        lambda: project.load('{"format":"yang-switchkit-project","format_version":99}'),
        "更新的版本")

    print("\n四、部分不认识时只丢那一条")

    mixed, warns = project.load("""
    {
      "format": "yang-switchkit-project",
      "format_version": 1,
      "vendor": "不存在的厂商",
      "order": ["vlan_port", "不存在的场景", "vlan_port"],
      "params": {
        "h3c": {
          "vlan_port": {"vlans": "10", "未来才有的参数": "x"},
          "未来才有的场景": {"a": "1"}
        },
        "未来才有的厂商": {"vlan_port": {"vlans": "20"}}
      }
    }
    """)
    check("未知厂商回落到默认",
          mixed.vendor == VENDORS[0].key, mixed.vendor)
    check("未知场景被丢掉",
          "未来才有的场景" not in mixed.params.get("h3c", {}))
    check("未知厂商的整块被丢掉",
          "未来才有的厂商" not in mixed.params)
    check("未知参数被丢掉，认识的还在",
          mixed.params["h3c"]["vlan_port"] == {"vlans": "10"},
          str(mixed.params.get("h3c", {}).get("vlan_port")))
    check("已生成列表去重且去掉未知项",
          mixed.order == ["vlan_port"], str(mixed.order))
    check("警告有记下来", len(warns) >= 3, f"{len(warns)} 条：{warns}")

    print("\n五、落盘")

    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "子目录" / "我的方案.json"
        written = project.to_file(target, original)
        check("自动建父目录并写出", written.exists(), str(written))
        back, warns2 = project.from_file(written)
        check("从文件读回一致",
              back.params["huawei"]["vlan_port"] == original.params["huawei"]["vlan_port"])
        check("读文件无警告", warns2 == [], str(warns2))

        bad = pathlib.Path(tmp) / "不是方案.txt"
        bad.write_text("vlan 10\n", encoding="utf-8")
        expect_error("选错文件被拒", lambda: project.from_file(bad), "JSON")

        missing = pathlib.Path(tmp) / "根本没有.json"
        expect_error("文件不存在被拒", lambda: project.from_file(missing), "读不了")

    print()
    if FAILS:
        print(f"结论：{len(FAILS)} 项未通过")
        return 1
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
