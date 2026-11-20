"""整机开局脚本编排。

问题在于：现有 29 个场景是**平铺**的，每一段都自带 ``system-view`` 开头和
``save force`` 结尾。单独看没问题，但真要拿来给一台新设备开局时，粘完的顺序
决定了它到底能不能通 —— 先配接口再建 VLAN，H3C 直接报"VLAN 不存在"；
先配生成树再配聚合，根桥位置就是错的。

这个模块负责把用户**已经生成**的那几段，按开局的实际依赖顺序重新串起来，
并且只保留一次保存动作。

它不生成任何新命令，只是重排 —— 命令的正确性仍然由 vendors 层负责。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from . import rollback
from .model import Block, GenResult

# --------------------------------------------------------------------------
# 阶段定义
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    """开局的一个阶段。

    ``required`` 为真的阶段缺失时，会明确报风险 —— 比如没有管理面配置，
    设备开局之后就再也登不上去了。
    """

    title: str
    keys: tuple
    required: bool = False
    hint: str = ""


#: 顺序就是交换机开局的真实依赖顺序，不要随手调整。
#:
#: 几条硬约束：
#:   · 堆叠 / IRF 会重启设备，必须在最前；
#:   · 管理面（VLAN + 管理地址 + SSH）先于所有业务配置，否则中途失联无法恢复；
#:   · VLAN 必须先于端口划分和三层接口；
#:   · 聚合必须先于生成树，否则根桥计算会把两条链路当成环；
#:   · 网关 / 三层接口先于路由协议宣告。
_STAGES: tuple = (
    Stage("① 堆叠与虚拟化", ("stacking",),
          hint="IRF / VSU 配置后需要重启，务必在维护窗口内先做完这一步"),
    Stage("② 设备标识与管理面", ("mgmt",), required=True,
          hint="管理 VLAN、管理地址、SSH 与账号，缺了它开局后无法远程登录"),
    Stage("③ 二层：VLAN 与端口", ("vlan_port",), required=True,
          hint="业务 VLAN 与端口划分，后续三层接口都依赖这里的 VLAN"),
    Stage("④ 二层：链路聚合", ("lag",),
          hint="先聚合再配生成树，否则双链路会被当成环路"),
    Stage("⑤ 二层：生成树与防环", ("stp", "loop_guard", "igmp_snooping")),
    Stage("⑥ 三层：网关与地址分配", ("gateway", "dhcp")),
    Stage("⑦ 三层：静态路由", ("static_route",)),
    Stage("⑧ 三层：动态路由", ("ospf", "isis", "bgp", "mplsvpn")),
    Stage("⑨ 二层安全", ("port_isolation", "port_security", "storm_control",
                        "dhcp_snooping", "arp_guard", "acl")),
    Stage("⑩ 接入认证", ("dot1x",)),
    Stage("⑪ 运维与可观测", ("syslog", "backup", "inspect", "mirror")),
    Stage("⑫ 其他业务配置", ("qos_rate", "qos_priority", "poe", "ipv6")),
)

#: 各家的保存命令 —— 整机脚本只在最后保留一次
_SAVE_WORDS = {"save force", "save", "write memory", "write"}

#: 回到用户视图的命令。整机脚本是一条连续的会话，中途回到用户视图没有意义，
#: 反而会让后面的段落不得不再来一次 system-view / configure terminal。
#: 全部去掉，只在最末尾补一条。
_BACK_WORDS = {"return", "end"}
_SAVE_CMD = {
    "h3c": "save force",
    "huawei": "save",
    "cisco": "write memory",
    "ruijie": "write",
}
_COMMENT = {"h3c": "#", "huawei": "#", "cisco": "!", "ruijie": "!"}

_VENDOR_NAMES = {
    "h3c": "H3C 新华三",
    "huawei": "华为",
    "cisco": "思科",
    "ruijie": "锐捷",
}


# --------------------------------------------------------------------------
# 编排
# --------------------------------------------------------------------------

def _strip_save(lines) -> list:
    """去掉段内的保存与回退命令：整机脚本只在结尾各保留一次。"""
    out: list = []
    for line in lines:
        body = str(line).strip().lower()
        if body in _SAVE_WORDS or body in _BACK_WORDS:
            continue
        out.append(line)
    return out


def _dedupe_enter(lines, vendor_key: str, state: Optional[dict] = None) -> list:
    """合并重复的「进入配置视图」命令。

    生成器是按区块组织的，每个区块都自带一条 ``system-view``，单独看是为了
    「每块都能独立粘贴」；但串成整机脚本后，同一段里会连着出现四五条
    ``system-view``，又长又容易让人怀疑是不是漏了什么。

    判断「当前在不在配置视图」需要知道哪些命令会进入子视图 —— 这件事回滚
    模块的规则表已经算清楚了（``UndoRule.view``），直接复用，不另起一套，
    免得以后加场景时两边不同步。
    """
    enter = ("configure terminal" if vendor_key in ("cisco", "ruijie")
             else "system-view")
    back = {"return", "end"}
    up = {"quit", "exit", "exit-address-family"}
    rules = rollback._compile_table(vendor_key)

    state = state if state is not None else {}
    out: list = []
    # 0 = 用户视图，1 = 系统 / 全局配置视图，>1 = 子视图。
    # 跨场景沿用上一次的层级，段与段之间才不会各来一条 system-view。
    depth = int(state.get("depth", 0))
    for line in lines:
        body = str(line).strip()
        if body == enter:
            if depth >= 1:
                continue
            depth = 1
            out.append(line)
            continue
        if body in back:
            depth = 0
            out.append(line)
            continue
        if body in up:
            depth = max(0, depth - 1)
            out.append(line)
            continue

        if depth == 0:
            out.append(enter)
            depth = 1
        out.append(line)

        rule, _m = rollback._match(rules, body)
        if rule is not None and rule.view:
            depth += 1

    state["depth"] = depth
    return out


def _scene_lines(result: GenResult, vendor_key: str = "",
                 state: Optional[dict] = None) -> list:
    """把一段生成结果拍平成命令行：去保存、合并重复的进入命令。"""
    lines: list = []
    for block in result.blocks:
        lines.extend(block.lines)
    lines = _strip_save(lines)
    if vendor_key:
        lines = _dedupe_enter(lines, vendor_key, state)
    return lines


def coverage(results: Mapping[str, GenResult]) -> dict:
    """返回编排统计，界面用它显示「几个阶段 / 缺了哪些」。"""
    present: list = []
    missing: list = []
    required_missing: list = []

    for stage in _STAGES:
        keys = [k for k in stage.keys if k in results]
        if keys:
            present.append((stage, keys))
        else:
            missing.append(stage)
            if stage.required:
                required_missing.append(stage)

    known = {k for stage in _STAGES for k in stage.keys}
    leftover = [k for k in results if k not in known]

    return {
        "present": present,
        "missing": missing,
        "required_missing": required_missing,
        "leftover": leftover,
        "scenes": sum(len(keys) for _stage, keys in present) + len(leftover),
        "stages": len(present),
    }


def build(vendor_key: str, results: Mapping[str, GenResult],
          vendor_name: str = "") -> GenResult:
    """把已生成的场景按开局依赖顺序编排成一份整机脚本。"""
    name = vendor_name or _VENDOR_NAMES.get(vendor_key, vendor_key)
    cc = _COMMENT.get(vendor_key, "#")

    result = GenResult(vendor_name=name, scenario_name="整机开局脚本")
    if not results:
        return result

    info = coverage(results)
    state: dict = {"depth": 0}

    for stage, keys in info["present"]:
        for index, key in enumerate(keys):
            scene = results[key]
            lines = _scene_lines(scene, vendor_key, state)
            if not lines:
                continue
            title = stage.title if index == 0 else f"{stage.title} · {scene.scenario_name}"
            comment = stage.hint if (index == 0 and stage.hint) else ""
            result.blocks.append(Block(title, lines, comment))

    # 注册表里没有的场景也不能悄悄丢掉
    for key in info["leftover"]:
        scene = results[key]
        lines = _scene_lines(scene, vendor_key, state)
        if lines:
            result.blocks.append(Block(f"其他 · {scene.scenario_name}", lines))

    # ---- 结尾的保存与提示 ----
    # 整份脚本是一条连续会话，末尾统一退回用户视图，保存命令才执行得了
    tail: list = ["return" if vendor_key in ("h3c", "huawei") else "end"]
    tail += ["", cc + " " + "=" * 64,
             cc + " 整机编排完成，确认配置无误后保存", cc + " " + "=" * 64,
             _SAVE_CMD.get(vendor_key, "save")]
    result.blocks.append(Block("", tail))

    if info["required_missing"]:
        for stage in info["required_missing"]:
            result.warnings.append(
                f"缺少「{stage.title}」这一段：{stage.hint}。"
                "整机脚本不完整，不要直接拿它开局。")
    if info["missing"]:
        names = "、".join(stage.title for stage in info["missing"]
                         if stage not in info["required_missing"])
        if names:
            result.notes.append(f"以下阶段本次未生成，已跳过：{names}。")

    result.notes.append(
        f"共编排 {info['scenes']} 个场景、{info['stages']} 个阶段。"
        "每段自带 system-view / return，段与段之间可以整段顺序粘贴。")
    result.notes.append(
        "下发前请先备份设备现有配置；堆叠与 IRF 相关配置可能需要重启才能生效。")
    return result
