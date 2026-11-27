"""AI 模块自检：全部离线，不联网、不需要 key。

跑法：

    python tools/ai_test.py

覆盖四件事：

1. **离线规则召回** —— ``verify.FIXED`` 那 7 条陷阱各造一个样本，必须全部命中。
   这是这个功能里唯一有「标准答案」的部分，所以用断言写死。
2. **脱敏** —— 密码、地址、MAC 都要被替换，行数和结构不能变。
3. **响应解析** —— 用假的 SSE 流和假的 JSON 回复，验证解析链路。
   不发真请求，所以断网也能跑，也不花钱。
4. **红线静态检查** —— 数据通路上不许出现配置命令关键字。
   「AI 不写命令」这条约定，靠这个检查变成机器可查的。

任何一项失败都会打印具体是哪一条，退出码为 1。
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scg.ai import advise, redact, rules, schema    # noqa: E402
from scg.ai.intent import _clean_params             # noqa: E402
from scg.ai.provider import AIError, Provider, extract_json   # noqa: E402
from scg import registry                            # noqa: E402

PASS: list = []
FAIL: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
    else:
        FAIL.append(f"{name}" + (f" —— {detail}" if detail else ""))


def find(findings, code: str):
    return [f for f in findings if f.rule == code]


# ==========================================================================
# 1. 离线规则
# ==========================================================================

CASES: tuple = (
    (
        "R3 基础 ACL 带协议字段（H3C）",
        "h3c",
        "system-view\nacl number 2000\n rule 0 permit ip source 1.1.1.1 0\nquit",
        "R3-acl",
    ),
    (
        "R3 基础 ACL 带目的地址（思科行内式）",
        "cisco",
        "configure terminal\naccess-list 10 permit tcp any any eq 80",
        "R3-acl",
    ),
    (
        "R3 华为 VLAN 镜像漏 ing",
        "huawei",
        "system-view\nmirror to observe-port 1 inbound",
        "R3-mirror",
    ),
    (
        "R3 华为 VLAN 镜像 both",
        "huawei",
        "system-view\nmirroring to observe-port 1 both",
        "R3-mirror",
    ),
    (
        "R3 锐捷未开 ssh-server",
        "ruijie",
        "configure terminal\nusername admin password admin\nline vty 0 4\nlogin local",
        "R3-ssh",
    ),
    (
        "R3 锐捷多余命令",
        "ruijie",
        "configure terminal\nno service telnet",
        "R3-telnet",
    ),
    (
        "R3 时区带符号冒号",
        "h3c",
        "system-view\nclock timezone UTC+08:00",
        "R3-timezone",
    ),
    (
        "R3 接口名被空格劈断",
        "h3c",
        "system-view\nospf 1\n silent-interface Vlan-interface\n silent-interface 10",
        "R3-split",
    ),
    (
        "R2 命令跑在系统视图",
        "h3c",
        "system-view\nport link-type trunk",
        "R2-view",
    ),
    (
        "R1 思科用户粘了 H3C 语法",
        "cisco",
        "system-view\nsysname SW1\nvlan 10\nport link-type trunk\nport trunk permit vlan 10",
        "R1-vendor",
    ),
)


def test_rules() -> None:
    for name, vendor, text, expect in CASES:
        findings, _unmatched = rules.check(vendor, text)
        hits = find(findings, expect)
        check(name, bool(hits), f"期望 {expect}，实际 {[f.rule for f in findings]}")

    # 正常配置不该有 error，这是误报率的护栏
    clean_h3c = (
        "system-view\n"
        "vlan 10\n"
        "quit\n"
        "interface GigabitEthernet1/0/1\n"
        " port link-type access\n"
        " port access vlan 10\n"
        "quit\n"
        "save force"
    )
    findings, _ = rules.check("h3c", clean_h3c)
    errors = [f for f in findings if f.level == rules.ERROR]
    check("正常 H3C 配置不误报", not errors,
          f"误报：{[(f.line, f.message) for f in errors]}")

    # 视图初始值：粘 display current-configuration 的输出（没有 system-view）
    # 不能把接口下的命令判成位置错误
    config_dump = (
        "#\n"
        "interface GigabitEthernet1/0/1\n"
        " port link-type trunk\n"
        " port trunk permit vlan 10\n"
        "#\n"
        "return"
    )
    findings, _ = rules.check("h3c", config_dump)
    view_err = find(findings, "R2-view")
    check("配置文件片段不误判视图", not view_err,
          f"误报：{[(f.line, f.message) for f in view_err]}")

    # R5 拼写错误
    findings, unmatched = rules.check("h3c", "system-view\nport link-trpe trunk")
    check("R5 抓拼写错误", bool(unmatched), "没有报未识别")
    if unmatched:
        check("R5 给出近似建议", unmatched[0]["suggest"].startswith("port link-type"),
              f"实际建议：{unmatched[0]['suggest']}")

    # R4 端口编号惯例
    findings, _ = rules.check("h3c", "system-view\ninterface GigabitEthernet0/0/1")
    check("R4 抓到华为式编号", bool(find(findings, "R4-port")), "没有报编号惯例")


# ==========================================================================
# 2. 脱敏
# ==========================================================================

def test_redact() -> None:
    text = (
        "sysname Core-SW-01\n"
        "local-user admin password cipher Admin@123\n"
        "interface GigabitEthernet1/0/1\n"
        " ip address 10.10.20.1 255.255.255.0\n"
        " port-security mac-address 0011-2233-4455\n"
    )
    out, count = redact.redact(text)

    check("脱敏替换了内容", count >= 4, f"只替换了 {count} 处")
    check("脱敏后不含原 IP", "10.10.20.1" not in out, out)
    check("脱敏后不含原密码", "Admin@123" not in out, out)
    check("脱敏后不含原 MAC", "0011-2233-4455" not in out, out)
    check("脱敏后不含原设备名", "Core-SW-01" not in out, out)
    check("脱敏不改变行数", len(out.splitlines()) == len(text.splitlines()),
          f"{len(text.splitlines())} → {len(out.splitlines())}")

    # 空文本不能崩
    empty, zero = redact.redact("")
    check("脱敏空输入", empty == "" and zero == 0)


# ==========================================================================
# 3. 响应解析
# ==========================================================================

def test_parse() -> None:
    # ```json 围栏
    check("解析围栏 JSON", extract_json('```json\n{"a": 1}\n```') == {"a": 1})

    # 前后带解释文字
    got = extract_json('好的，结果如下：\n{"a": 2}\n以上。')
    check("解析带前后文的 JSON", got == {"a": 2})

    # 非 JSON 必须报 bad_format，不能静默返回空
    try:
        extract_json("抱歉，我无法回答")
    except AIError as exc:
        check("非 JSON 报 bad_format", exc.code == "bad_format", exc.code)
    else:
        check("非 JSON 报 bad_format", False, "没有抛异常")

    # SSE 解析
    stream_lines = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"{\\"a\\":"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"3}"}}]}\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
        b'data: [DONE]\n',
    ]
    provider = Provider.__new__(Provider)
    provider.cfg = None
    chunks: list = []
    provider.on_chunk = chunks.append
    text = provider._read_stream(io.BytesIO(b"".join(stream_lines)), float("inf"))
    check("SSE 拼出完整文本", text == '{"a":3}', text)
    check("SSE 触发增量回调", len(chunks) == 2, str(chunks))

    # 模型给的 findings 要洗一遍：越界行号归零、非法级别兜底、空 message 丢掉
    cleaned = advise.clean_findings([
        {"line": 9999, "level": "warn", "message": "越界行号"},
        {"line": 2, "level": "严重", "message": "非法级别"},
        {"line": 1, "level": "high", "message": ""},
        "不是字典",
    ], total_lines=5)
    check("AI 行号越界归零", cleaned and cleaned[0]["line"] == 0,
          str([f["line"] for f in cleaned]))
    check("AI 非法级别兜底", cleaned and cleaned[1]["level"] == "low",
          str([f["level"] for f in cleaned]))
    check("AI 丢弃空条目", len(cleaned) == 2, str(len(cleaned)))

    # 模型不按格式回时，原文要保留下来给界面看
    result = advise.review("", "h3c", [], None)
    check("空输入不抛异常", result.get("error") == "没有内容可分析", str(result.get("error")))


# ==========================================================================
# 4. schema 与参数清洗
# ==========================================================================

def test_schema() -> None:
    scenario = registry.get("vlan_port")
    data = schema.scenario_schema(scenario)

    check("schema 是 object", data.get("type") == "object")
    props = data.get("properties") or {}
    check("schema 覆盖全部参数",
          set(props) == {p.key for p in scenario.params},
          f"{set(props)} vs {[p.key for p in scenario.params]}")

    # 所有值必须是 string —— store 里存的都是 str
    bad = [k for k, v in props.items() if v.get("type") != "string"]
    check("schema 全部 string", not bad, f"不是 string 的：{bad}")

    # 选项导出的是 value 而不是中文标签
    port_type = props.get("port_type") or {}
    check("选项导出 value", port_type.get("enum") == ["access", "trunk"],
          str(port_type.get("enum")))

    # 第一轮清单要含全部场景
    catalog = schema.catalog_text()
    missing = [s.key for s in registry.SCENARIOS if s.key not in catalog]
    check("清单覆盖全部场景", not missing, f"缺：{missing}")

    # 参数清洗：非法 key / 非法选项 / 非数字都要丢掉
    clean, dropped = _clean_params(scenario, {
        "vlans": "10,20",
        "port_type": "trunk",
        "ports": "1/0/1-8",
        "bogus": "x",
    })
    check("清洗保留合法值", clean.get("vlans") == "10,20" and clean.get("port_type") == "trunk",
          str(clean))
    check("清洗丢弃非法 key", "bogus" not in clean, str(clean))
    check("清洗记录丢弃项", len(dropped) == 1, str(dropped))

    # 非法选项
    clean2, dropped2 = _clean_params(scenario, {"port_type": "混合模式"})
    check("清洗丢弃非法选项", "port_type" not in clean2 and dropped2, str(dropped2))

    # 数字型参数只收数字 —— vlan_port 里没有 NUM，换 lag 的聚合组号来测
    lag = registry.get("lag")
    clean3, dropped3 = _clean_params(lag, {"group": "abc", "mode": "lacp"})
    check("清洗丢弃非数字", "group" not in clean3 and clean3.get("mode") == "lacp",
          f"{clean3} / {dropped3}")


# ==========================================================================
# 5. 配置往返：存盘 → 读回 → ready() 结论必须一致
# ==========================================================================

def test_config_roundtrip() -> None:
    """这条链路是 3.1.0 出事的地方，专门盯着。

    当时的 bug：用户填了密钥、测试连接也通过，主界面还是显示「纯离线」。
    原因是配置里有个 ``enabled`` 开关默认关着，而 ``ready()`` 要求它也为真。
    自检当时只覆盖「解析、脱敏、规则」，没覆盖「存盘再读回来还能不能用」，
    所以 51 项全绿，用户一上手就撞上。

    现在多两件事：验证 ready() 只看密钥，以及配置文件里不许再出现
    enabled 字段（防止哪天又被加回去）。
    """
    from scg.ai.config import AIConfig, load, save

    base = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}

    check("有密钥即 ready", AIConfig(key="sk-test", **base).ready())
    check("无密钥不 ready", not AIConfig(key="", **base).ready())
    check("无模型不 ready",
          not AIConfig(key="sk-test", base_url="https://x/v1", model="").ready())

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "ai.json"

        save(AIConfig(key="sk-roundtrip-123456", **base), path)
        restored = load(path)
        check("存盘后密钥能读回", restored.key == "sk-roundtrip-123456",
              f"读回长度 {len(restored.key)}")
        check("存盘后 ready 一致", restored.ready(), "读回后 ready 为假")
        check("模型与地址读回一致",
              restored.model == "deepseek-chat" and restored.base_url == base["base_url"],
              f"{restored.model} / {restored.base_url}")

        raw = json.loads(path.read_text(encoding="utf-8"))
        check("配置文件不含 enabled 字段", "enabled" not in raw, str(sorted(raw)))

        # 清空密钥后再读回，应该回到不可用
        save(AIConfig(key="", **base), path)
        check("清空密钥后不 ready", not load(path).ready())

        # 坏文件不能让程序起不来
        path.write_text("{ 这不是 json", encoding="utf-8")
        check("坏配置退回默认值", not load(path).ready())


# ==========================================================================
# 6. 红线：数据通路上不许出现配置命令
# ==========================================================================
#: 这些文件是「模型输出 → 用户」的数据通路，里面不能有命令关键字。
#: rules.py 不在此列：它的职责就是拿命令关键字当匹配模式。
FORBIDDEN_IN = ("provider.py", "intent.py")
COMMAND_TOKENS = (
    "system-view", "port link-type", "switchport mode", "interface Gi",
    "ip route-static", "display current", "configure terminal",
)


def test_redline() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent / "scg" / "ai"
    for fname in FORBIDDEN_IN:
        path = root / fname
        if not path.is_file():
            check(f"红线检查 {fname}", False, "文件不存在")
            continue
        body = path.read_text(encoding="utf-8")
        hits = [t for t in COMMAND_TOKENS if t in body]
        check(f"红线检查 {fname}", not hits, f"出现命令关键字：{hits}")

    # 整个包里不允许 import vendors.generate —— 那是拼命令的入口
    for path in root.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        check(f"{path.name} 不调用 generate",
              "vendors.generate(" not in body and "from ..vendors import generate" not in body,
              "出现了命令生成调用")


# ==========================================================================
# main
# ==========================================================================

def main() -> int:
    test_rules()
    test_redact()
    test_parse()
    test_schema()
    test_config_roundtrip()
    test_redline()

    print(f"通过 {len(PASS)} 项")
    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：")
        for item in FAIL:
            print(f"  ✗ {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
