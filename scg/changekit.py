"""变更包：把一次变更要交付的东西打包成一份文本。

一份变更交付物有五样
------------------
1. 变更申请单（谁、什么时候、为什么、影响什么）
2. 配置命令
3. 回滚命令
4. 验收步骤
5. 影响范围

这个工具原来只产出第 2 样。第 3、4 样的能力其实早就有了——``rollback.py``
能反推撤销脚本，``accept.py`` 能给验收命令——但都散在不同页签里，
要用的时候得挨个点一遍、再手工拼到一起。

这个模块把它们串成一条线：一次导出，五样齐。

为什么是纯文本不是 docx
----------------------
变更单的正文是要贴进 OA 或工单系统的，纯文本能直接复制、能版本对比。
真要排版再粘进 Word 就行，反过来（从 docx 抠纯文本）反而麻烦。

为什么命令区不带注释
------------------
交付给执行的脚本应该能整段粘到设备上，中间夹着中文说明会打断粘贴。
说明放在「影响范围」和「验收清单」里，各司其职。
"""
from __future__ import annotations

from datetime import datetime

from . import __version__, accept, registry, rollback


#: 影响范围里值得单列的参数。没列到的参数不进影响范围——
#: 一堆默认值堆在那里只会让人看不清重点
_IMPACT = (
    ("vlans", "VLAN"),
    ("vlan_id", "VLAN"),
    ("allowed", "放通 VLAN"),
    ("ports", "端口"),
    ("edge_ports", "边缘端口"),
    ("trust_ports", "信任端口"),
    ("user_ports", "用户端口"),
    ("acl_id", "ACL 编号"),
    ("group", "组号"),
    ("routes", "路由条目"),
    ("networks", "宣告网段"),
    ("mgmt_ip", "管理地址"),
    ("virtual_ip", "虚拟网关"),
    ("server", "服务器"),
    ("sysname", "主机名"),
)

_RULE = "=" * 66
_THIN = "-" * 66


def build(vendor_key: str, vendor_name: str, items: list, results: dict,
          snap=None, title: str = "") -> list:
    """生成变更包的文本行。

    ``items`` 是 ``[(scenario_key, params), ...]``，按生成顺序；
    ``results`` 是 ``{scenario_key: GenResult}``；``snap`` 可选，
    有的话会把现网现状写进「变更前状态」。
    """
    items = [(k, p or {}) for k, p in items if results.get(k)]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    out: list = [
        _RULE,
        "Yang-SwitchKit 变更包",
        f"厂商：{vendor_name}",
        f"场景：{len(items)} 个" + (f"　|　{title}" if title else ""),
        f"生成时间：{now}　|　工具版本：V{__version__}",
        _RULE,
        "",
        "使用顺序：核对「一」→ 执行「二」→ 出问题跑「三」→ 用「四」验收。",
        "任何一步不符合预期，先停下排查，不要继续往下走。",
        "",
    ]

    out.extend(_section_before(snap))
    out.extend(_section_commands(items, results))
    out.extend(_section_rollback(vendor_key, vendor_name, items, results))
    out.extend(_section_accept(vendor_key, items))
    out.extend(_section_impact(items))

    out.append(_RULE)
    out.append("本文件由 Yang-SwitchKit 生成。命令来自本地核验过的模板，")
    out.append("参数取自你填写的内容；工具不连接设备，下发前请在模拟器上验证。")
    out.append(_RULE)
    return out


# ==========================================================================
# 各段
# ==========================================================================

def _section_before(snap) -> list:
    out = ["━━ 一、变更前状态 ━━", ""]
    if snap is None:
        out.append("（没有导入现网配置。如果要留档，可以在「AI 助手」页用"
                   "「从现网导入」先读一次现状。）")
        out.append("")
        return out

    from . import snapshot as snapmod
    out.extend(snapmod.summary_lines(snap))
    out.append("")
    return out


def _section_commands(items: list, results: dict) -> list:
    out = ["━━ 二、配置命令 ━━", "",
           "以下命令按场景分段，整段可以直接粘到设备上执行。", ""]

    for index, (key, _params) in enumerate(items, 1):
        result = results.get(key)
        if result is None:
            continue
        scenario = registry.get(key)
        out.append(_THIN)
        out.append(f"【{index}】{scenario.name}　（{scenario.group}）")
        out.append(_THIN)
        try:
            body = result.to_text(header=False, comments=False)
        except Exception:  # noqa: BLE001
            body = ""
        out.extend(body.splitlines() if body.strip() else ["（这一段没有产出命令）"])
        out.append("")

    return out


def _section_rollback(vendor_key: str, vendor_name: str, items: list,
                      results: dict) -> list:
    out = ["━━ 三、回滚命令 ━━", "",
           "以下命令把「二」里的配置撤销掉，按逆序排列，后配的先撤。", ""]

    keys = [k for k, _ in items]
    payload = [results.get(k) for k in keys]
    payload = [r for r in payload if r is not None]
    if not payload:
        out.extend(["（没有可回滚的内容）", ""])
        return out

    try:
        undo = rollback.build(vendor_key, payload,
                              scenario_name=f"变更包（{len(payload)} 个场景）",
                              vendor_name=vendor_name)
        body = undo.to_text(comments=False)
    except Exception as exc:  # noqa: BLE001
        out.extend([f"（回滚脚本没能生成：{exc}）", ""])
        return out

    if not body.strip():
        out.extend(["（这一段没有可自动撤销的命令）", ""])
        return out

    out.extend(body.splitlines())
    out.append("")
    out.append("说明：撤销后回到的是「默认值」，不一定等于变更前的原状态。"
               "要精确恢复请载入变更前配置用「配置回滚」页。")
    out.append("")
    return out


def _section_accept(vendor_key: str, items: list) -> list:
    try:
        lines = accept.render(vendor_key, items)
    except Exception as exc:  # noqa: BLE001
        return ["━━ 四、验收清单 ━━", "", f"（没能生成：{exc}）", ""]

    # accept.render 给自己的标题不带序号（它也能单独用），
    # 编号由这一层加，免得验收模块知道自己在变更包里排第几
    if lines:
        lines = list(lines)
        lines[0] = "━━ 四、验收清单 ━━"
    return lines


def _section_impact(items: list) -> list:
    out = ["━━ 五、影响范围 ━━", ""]
    seen: set = set()
    rows: list = []

    for key, params in items:
        scenario = registry.get(key)
        for param_key, label in _IMPACT:
            value = str(params.get(param_key, "") or "").strip()
            if not value:
                continue
            text = value.replace("\n", "；")
            if len(text) > 60:
                text = text[:60] + "…"
            row = f"{scenario.name}　{label}：{text}"
            if row in seen:
                continue
            seen.add(row)
            rows.append(row)

    if not rows:
        out.extend(["（这次变更没有可列举的关键参数）", ""])
        return out

    out.extend(rows)
    out.append("")
    out.append("核对要点：上面这些是这次会动的对象。下发前确认它们都在变更窗口内，")
    out.append("不在的（比如生产核心口）先确认再动。")
    out.append("")
    return out


# ==========================================================================
# 文件名
# ==========================================================================

def filename(vendor_name: str, items: list) -> str:
    """给导出用的默认文件名。带上时间，同一个场景改两次不会互相覆盖。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    names = []
    for key, _ in items[:2]:
        try:
            names.append(registry.get(key).name)
        except Exception:  # noqa: BLE001
            continue
    tag = "-".join(names) if names else "变更"
    # 文件名里不能有路径分隔符
    tag = tag.replace("/", "_").replace("\\", "_").replace(":", "_")
    more = f"等{len(items)}项" if len(items) > 2 else ""
    return f"变更包-{vendor_name}-{tag}{more}-{stamp}.txt"
