"""配置回滚（undo）脚本推导。

设计目标：**任何一段由本工具生成的配置，都能反推出一份可执行的撤销脚本。**

做法是规则表驱动的「配置逆向」：

1. 把生成的命令行按视图层级解析成一棵树（系统视图 / 接口视图 / 协议视图 …）；
2. 每一条命令交给该厂商的规则表匹配，得到它的 undo 写法与可靠程度；
3. 整棵树**逆序**输出，需要时自动补 ``interface X`` / ``quit`` 切换视图。

两条不可动摇的原则：

* **能整体删除的视图就整块删**。``vlan 10`` 下挂着 ``name`` 之类的子命令时，
  只需一条 ``undo vlan 10``，不必逐条撤子命令 —— 那既啰嗦又容易漏。
* **撤销方式拿不准就标出来，不假装精确**。规则分三档：
  ``exact``（写法确定）、``approx``（撤销后回到默认值，可能与原状态不同）、
  ``manual``（无法自动撤销，只给人工提示）。宁可让网工多看一眼，
  也不能让一条错误回滚把设备改成更难恢复的状态。

规则表按厂商分开写，因为四家的 undo 语义差异比想象中大：
思科 / 锐捷用 ``no`` 前缀且普遍允许保留完整参数，H3C / 华为用 ``undo`` 前缀
且部分命令撤销时**不能**带参数（带了直接报错）。共用一张表迟早出事。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .model import Block, GenResult

# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UndoRule:
    """一条「命令 -> 撤销命令」的映射。

    ``rx``    匹配 strip 后的命令。**不能带前导空格**，因为解析前已经 strip，
              带了就永远匹配不上（与 explain 模块踩过的是同一个坑）。
    ``undo``  None = 不产生回滚项（会话类 / 查询类 / 激活动作）；
              "" = 视图容器，本身不能删，需要逐条撤子命令；
              其他 = 撤销模板，``{1}`` ``{2}`` 引用正则捕获组。
    ``view``  该命令会进入子视图，其后的命令都属于它，直到退出。
    ``conf``  exact / approx / manual / skip。
    ``note``  给 approx / manual 的说明，会写进脚本注释。
    ``overwrite`` 该命令是「覆盖型」的：重下一遍就等于改回原值。只有这类命令
              能用基线配置做精确回滚；像 ``port trunk permit vlan`` 这种增量型
              命令，重下只会往上加，撤不干净。
    """

    rx: str
    undo: Optional[str] = None
    view: bool = False
    conf: str = "exact"
    note: str = ""
    overwrite: bool = False

    def compile(self):
        return re.compile(self.rx)


@dataclass
class _Cmd:
    text: str
    rule: UndoRule


@dataclass
class _Frame:
    """一个子视图：进入命令 + 挂在它下面的元素。"""

    enter: str
    rule: UndoRule
    children: list = field(default_factory=list)


@dataclass
class _Item:
    """渲染单元：一条待执行的撤销命令，以及它必须在哪个视图里执行。"""

    text: str
    ctx: tuple
    conf: str
    note: str
    source: str
    section: str = ""


#: 未识别命令的统一规则：保留下来，标 manual，交给人工判断
_UNKNOWN = UndoRule(rx="", undo=None, conf="manual",
                    note="未识别的命令，请对照手册确认撤销写法")


# --------------------------------------------------------------------------
# 命令拆分：把「参数」从命令里挖掉，留下稳定的键
# --------------------------------------------------------------------------

def split_key(text: str, vendor_key: str) -> tuple:
    """把一条命令拆成「键模板」和「取值」。

    ``port trunk permit vlan 10 20`` -> ``("port trunk permit vlan <>", "10 20")``

    做法是复用命令正则：那里已经把参数部分圈成捕获组，把这些组从原命令里
    挖掉，剩下的就是与具体取值无关的键。基线和回滚脚本都走这一套，两边
    才能对齐。
    """
    rules = _compile_table(vendor_key)
    rule, match = _match(rules, text)
    if rule is None or match is None or rule.view:
        return text, ""

    spans = []
    for index in range(1, (match.lastindex or 0) + 1):
        span = match.span(index)
        if span != (-1, -1):
            spans.append(span)
    if not spans:
        return text, ""

    key = text
    for start, end in sorted(spans, reverse=True):
        key = key[:start] + "<>" + key[end:]

    values = [match.group(i) for i in range(1, (match.lastindex or 0) + 1)
              if match.group(i)]
    return key.strip(), " ".join(v.strip() for v in values)


def _range_members(enter: str) -> list:
    """把 ``interface range`` 展开成单个接口名。

    生成器为了少写几行,常用 ``interface range A to B``;而设备上导出的配置
    永远是逐个接口一条。两边对不上，精确回滚就永远查不到原值 —— 所以这里
    必须把范围摊开。

    两种写法都要认：
      · ``GigabitEthernet1/0/1 to GigabitEthernet1/0/8``（H3C / 华为）
      · ``GigabitEthernet1/0/1 - 8`` 与 ``GigabitEthernet1/0/1-8``（思科 / 锐捷）
    """
    match = re.match(r"^interface range\s+(.+)$", enter.strip(), re.I)
    if not match:
        return []

    out: list = []
    for chunk in re.split(r"[,，]", match.group(1)):
        chunk = chunk.strip()
        if not chunk:
            continue

        full = re.match(r"^(.*?)(\d+)\s+to\s+(.*?)(\d+)$", chunk, re.I)
        if full:
            prefix, start, right_prefix, end = (
                full.group(1), int(full.group(2)), full.group(3), int(full.group(4)))
            if right_prefix.strip() != prefix.strip():
                out.append(chunk)
                continue
            out.extend(f"{prefix}{n}" for n in range(start, end + 1))
            continue

        short = re.match(r"^(.*?)(\d+)\s*-\s*(\d+)$", chunk)
        if short:
            prefix, start, end = short.group(1), int(short.group(2)), int(short.group(3))
            if start <= end <= start + 512:
                out.extend(f"{prefix}{n}" for n in range(start, end + 1))
                continue
        out.append(chunk)
    return out


# --------------------------------------------------------------------------
# 基线：变更前的配置
# --------------------------------------------------------------------------

@dataclass
class Baseline:
    """变更前的配置基线。

    有它之后，回滚就不只是「撤销回默认值」，而是「改回你原来的值」。举个
    最典型的例子：端口原本是 hybrid，这次改成 trunk，没有基线时只能给
    ``undo port link-type``（回 access，错的），有基线就能给
    ``port link-type hybrid``（对的）。
    """

    vendor_key: str = ""
    entries: dict = field(default_factory=dict)
    contexts: int = 0

    @classmethod
    def parse(cls, text: str, vendor_key: str) -> "Baseline":
        base = cls(vendor_key=vendor_key)
        rules = _compile_table(vendor_key)
        ctx = ""
        seen_ctx: set = set()

        for raw in (text or "").splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            head = line.strip()
            if head in ("#", "!"):
                ctx = ""
                continue
            if head.startswith(("#", "!")):
                continue

            rule, _match_obj = _match(rules, head)
            if rule is not None and rule.view:
                ctx = head
                seen_ctx.add(head)
                # 视图容器本身也记一条：回滚时要靠它区分「新增的 VLAN」和
                # 「本来就有、只是改了内容」，前者能整块删，后者不能。
                base.entries[("", head)] = head
                continue

            # 子命令靠缩进判断；没有缩进的是顶层命令
            if not line[:1] in (" ", "\t"):
                ctx = ""

            key, _value = split_key(head, vendor_key)
            base.entries[(ctx, key)] = head

        base.contexts = len(seen_ctx)
        return base

    def __len__(self) -> int:
        return len(self.entries)

    def has_view(self, ctx: tuple, enter: str) -> bool:
        """这个视图在变更前是否已经存在。"""
        return ("", enter) in self.entries

    def restore_group(self, ctx: tuple, cmd_text: str) -> list:
        """查这条命令在变更前的原写法，返回 ``[(视图路径, 原命令行), ...]``。

        返回空列表表示查不到（说明这条配置是本次新增的，撤销即可）。

        ``interface range`` 要特别小心：生成器为省行数会把多个端口合成一条，
        但设备上这些端口原来的配置未必相同。这时硬套第一条的原值会把另外两个
        端口改错 —— 所以按成员逐个查，值一致才合并回一条，不一致就拆成独立的
        接口块。凑不齐（有端口查不到原值）就整条放弃，交回 undo 处理，
        宁可粗糙也不能改错。
        """
        key, _value = split_key(cmd_text, self.vendor_key)

        if not ctx:
            found = self.entries.get(("", key))
            return [(ctx, found)] if found and found != cmd_text else []

        head = ctx[-1]
        members = _range_members(head)

        if not members:
            found = self.entries.get((head, key))
            return [(ctx, found)] if found and found != cmd_text else []

        pairs = []
        for member in members:
            found = self.entries.get((f"interface {member}", key))
            if not found:
                return []          # 有成员查不到原值，整条放弃
            pairs.append((("interface " + member,), found))

        values = {text for _ctx, text in pairs}
        if len(values) == 1:
            return [(ctx, pairs[0][1])]
        return pairs


# --------------------------------------------------------------------------
# 各家 CLI 的会话词
# --------------------------------------------------------------------------

_EXIT = {
    "h3c": {"quit"},
    "huawei": {"quit"},
    "cisco": {"exit", "exit-address-family"},
    "ruijie": {"exit", "exit-address-family"},
}

_HARD_EXIT = {
    "h3c": {"return"},
    "huawei": {"return"},
    "cisco": {"end"},
    "ruijie": {"end"},
}

_SESSION = {
    "h3c": {"system-view", "save", "save force", "quit", "return"},
    "huawei": {"system-view", "save", "quit", "return"},
    "cisco": {"configure terminal", "conf t", "write", "write memory", "end", "exit"},
    "ruijie": {"configure terminal", "conf t", "write", "end", "exit"},
}

#: 查询 / 文件传输 / 复位类，撤销无从谈起
_QUERY_HEADS = {
    "display", "show", "more", "dir", "ping", "tracert", "traceroute",
    "telnet", "ftp", "tftp", "copy", "put", "get", "debugging", "reset",
    "clear", "startup", "backup", "archive", "save", "write", "reload", "reboot",
}


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def _is_query(text: str) -> bool:
    toks = text.split()
    if not toks:
        return True
    head = toks[0].lower()
    # undo 开头的是配置动作（如 undo shutdown），不能当查询丢掉
    return text.lower().startswith("undo ") is False and head in _QUERY_HEADS


def _match(rules, text: str):
    for rule, compiled in rules:
        m = compiled.match(text)
        if m:
            return rule, m
    return None, None


def _fill(template: str, match) -> str:
    """把 ``{1}`` ``{2}`` 换成捕获组内容，并压掉多余空格。"""
    out = template
    for index, group in enumerate(match.groups(), 1):
        out = out.replace("{%d}" % index, (group or "").strip())
    # 模板里可能留下空的尾随段（如 {2} 没捕获到）
    return " ".join(out.split())


def _parse(lines, vendor_key: str, rules):
    """命令行 -> 视图树。返回顶层元素列表。"""
    exits = _EXIT.get(vendor_key, {"quit"})
    hard = _HARD_EXIT.get(vendor_key, {"return"})
    session = _SESSION.get(vendor_key, set())

    top: list = []
    stack: list = []

    for raw in lines:
        text = str(raw).rstrip()
        if not text.strip():
            continue
        if text.lstrip()[:1] in ("#", "!"):
            continue
        body = text.strip()

        if body in hard:
            stack.clear()
            continue
        if body in exits:
            if stack:
                stack.pop()
            continue
        if body in session:
            continue
        if _is_query(body):
            continue

        rule, match = _match(rules, body)

        if rule is None:
            (stack[-1].children if stack else top).append(_Cmd(body, _UNKNOWN))
            continue

        if rule.conf == "skip" or (rule.undo is None and rule.conf != "manual"):
            continue

        if rule.view:
            frame = _Frame(body, rule)
            (stack[-1].children if stack else top).append(frame)
            stack.append(frame)
            continue

        (stack[-1].children if stack else top).append(_Cmd(body, rule))

    return top


def _global_undo(rule: UndoRule, source: str, rules) -> str:
    """视图整体删除时，用进入命令的捕获组填充 undo 模板。"""
    if not rule.undo:
        return ""
    _rule, m = _match(rules, source)
    return _fill(rule.undo, m) if m else rule.undo


def _reversed_items(tree, ctx: tuple, rules, section: str = "",
                    baseline=None) -> list:
    """逆序遍历视图树，摊平成撤销清单。"""
    out: list = []
    for element in reversed(tree):
        if isinstance(element, _Cmd):
            out.extend(_make_item(element, ctx, rules, section, baseline))
            continue

        # 视图可以整体删除：一条 undo 覆盖下面所有子命令。
        # 但如果基线里本来就有这个视图（原来就有 vlan 10 / 这个接口），
        # 整块删掉就把别人原来的配置一起端了 —— 这时只能逐条撤子命令。
        if element.rule.undo and not (baseline and baseline.has_view(ctx, element.enter)):
            text = _global_undo(element.rule, element.enter, rules)
            if text:
                out.append(_Item(text, ctx, element.rule.conf,
                                 element.rule.note or "整块删除该视图",
                                 element.enter, section))
            continue

        # 视图删不掉（接口、line vty 等），或基线里原本就存在：逐条撤子命令
        out.extend(_reversed_items(element.children, ctx + (element.enter,),
                                   rules, section, baseline))
    return out


def _make_item(cmd: _Cmd, ctx: tuple, rules, section: str = "",
               baseline=None) -> list:
    """返回撤销项列表。

    正常情况下只有一条；``interface range`` 里各端口原配置不同时，会拆成
    每个端口一条，各自恢复到自己的原值。
    """
    rule = cmd.rule

    # 覆盖型命令且基线里有原值：直接改回原值，比 undo 精确
    if baseline is not None and rule.overwrite:
        restored = baseline.restore_group(ctx, cmd.text)
        if restored:
            return [_Item(text, where, "exact", "恢复变更前的取值",
                          cmd.text, section)
                    for where, text in restored]

    if rule.undo:
        if "{" in rule.undo:
            _rule, m = _match(rules, cmd.text)
            text = _fill(rule.undo, m) if m else rule.undo
        else:
            text = rule.undo
        return [_Item(text, ctx, rule.conf, rule.note, cmd.text, section)]

    # 无法自动撤销：保留原命令，交给人工
    return [_Item("# [人工确认] " + cmd.text, ctx, "manual",
                  rule.note or "未识别的命令，请对照手册确认撤销写法",
                  cmd.text, section)]


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

_INDENT = " "

_ENTER_ROOT = {
    "h3c": "system-view",
    "huawei": "system-view",
    "cisco": "configure terminal",
    "ruijie": "configure terminal",
}
_QUIT_WORD = {"h3c": "quit", "huawei": "quit", "cisco": "exit", "ruijie": "exit"}
_END_WORD = {"h3c": "return", "huawei": "return", "cisco": "end", "ruijie": "end"}
_SAVE = {
    "h3c": ["save force"],
    "huawei": ["save"],
    "cisco": ["write memory"],
    "ruijie": ["write"],
}

#: 注释符必须按厂商来。思科 / 锐捷的 `#` 不是注释符，会被当成命令执行并报错，
#: 所以回滚脚本里绝不能沿用 H3C 那套 `#`。
_COMMENT = {"h3c": "#", "huawei": "#", "cisco": "!", "ruijie": "!"}


def _as_comment(text: str, cc: str) -> str:
    """把内部统一用 # 写的注释行，换成该厂商的注释符。"""
    if text.startswith("# "):
        return cc + " " + text[2:]
    if text == "#":
        return cc
    return text


def _render(items, vendor_key: str, scenario_name: str, vendor_name: str) -> GenResult:
    result = GenResult(vendor_name=vendor_name, scenario_name=scenario_name)

    quit_word = _QUIT_WORD.get(vendor_key, "quit")
    cc = _COMMENT.get(vendor_key, "#")
    lines: list = [_ENTER_ROOT.get(vendor_key, "system-view")]
    cur: tuple = ()
    last_section = None

    for item in items:
        # 换场景时先退回根视图再打标题，标题才不会夹在接口视图里面
        if item.section and item.section != last_section:
            total = len(cur)
            for step in range(total):
                lines.append(_INDENT * (total - 1 - step) + quit_word)
            cur = ()
            lines.append("")
            lines.append(cc + " ---- 撤销：" + item.section + " ----")
            last_section = item.section

        # 视图切换：先退回公共前缀，再逐层进入
        if item.ctx != cur:
            common = 0
            while (common < len(cur) and common < len(item.ctx)
                   and cur[common] == item.ctx[common]):
                common += 1
            total = len(cur)
            for step in range(total - common):
                lines.append(_INDENT * (total - 1 - step) + quit_word)
            for depth, enter in enumerate(item.ctx[common:], common):
                lines.append(_INDENT * depth + enter)
            cur = item.ctx

        depth = len(cur)
        lines.append(_INDENT * depth + _as_comment(item.text, cc))
        if item.conf == "approx":
            lines.append(_INDENT * depth + cc + " [近似] " + item.note)
        elif item.conf == "manual":
            lines.append(_INDENT * depth + cc + " [人工确认] " + item.note)

    # 收尾：逐级退出
    total = len(cur)
    for step in range(total):
        lines.append(_INDENT * (total - 1 - step) + quit_word)

    lines.append(_END_WORD.get(vendor_key, "return"))
    lines.append("")
    lines.append(cc + " [确认] 回滚结果核对无误后，再执行下面这条保存")
    lines.extend(_SAVE.get(vendor_key, ["save"]))

    result.blocks.append(Block("", lines))

    manual = [i for i in items if i.conf == "manual"]
    approx = [i for i in items if i.conf == "approx"]
    if manual:
        result.warnings.append(
            f"有 {len(manual)} 条命令无法自动推导撤销写法，已在脚本中以 [人工确认] 标出。"
            "这些行是注释、不会被执行，请手工核对后再下发。")
    if approx:
        result.warnings.append(
            f"有 {len(approx)} 条属于近似撤销（撤销后回到设备默认值，可能与原状态不同），"
            "已在脚本中以 [近似] 标出。")
    result.notes.append(
        "回滚脚本按原配置的逆序排列：后配的先撤。执行前建议先存档一份现网配置，"
        "避免回滚本身也需要回滚。")
    return result


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------

_VENDOR_NAMES = {
    "h3c": "H3C 新华三",
    "huawei": "华为",
    "cisco": "思科",
    "ruijie": "锐捷",
}


def build(vendor_key: str, results, scenario_name: str = "",
          vendor_name: str = "", baseline=None) -> GenResult:
    """根据已生成的配置推导回滚脚本。

    ``results`` 可以是单个 GenResult，也可以是 GenResult 列表（多场景合并）。
    多场景时按**逆序**逐场景处理：最后生成的场景最先回滚。
    """
    if isinstance(results, GenResult):
        results = [results]
    results = [r for r in results if r is not None and r.blocks]
    if not results:
        return _render([], vendor_key, scenario_name or "（无内容）", vendor_name
                       or _VENDOR_NAMES.get(vendor_key, vendor_key))

    rules = _compile_table(vendor_key)
    items: list = []
    for res in reversed(results):
        scene_lines: list = []
        for block in res.blocks:
            scene_lines.extend(block.lines)
        tree = _parse(scene_lines, vendor_key, rules)
        items.extend(_reversed_items(tree, (), rules, res.scenario_name, baseline))

    name = vendor_name or _VENDOR_NAMES.get(vendor_key, vendor_key)
    title = scenario_name or "已生成的全部场景"
    result = _render(items, vendor_key, title, name)

    if baseline is not None:
        exact = sum(1 for item in items if item.note == "恢复变更前的取值")
        result.notes.insert(0, f"已载入变更前配置（{len(baseline)} 条），"
                               f"本次有 {exact} 条按原值精确恢复，其余照常撤销。")
        if exact == 0:
            result.warnings.append(
                "基线里没有匹配到可精确恢复的命令。常见原因是接口写法不一致 ——"
                "生成时用的端口名要和设备上 display interface brief 的输出逐字相同。")
    return result


def _compile_table(vendor_key: str):
    return _COMPILED.get(vendor_key) or _COMPILED["h3c"]


def _compile(rules):
    return tuple((rule, rule.compile()) for rule in rules)


# --------------------------------------------------------------------------
# 规则表
# --------------------------------------------------------------------------
#
# 书写顺序 = 匹配优先级：**具体规则必须排在通用规则前面**，
# 否则 `network X mask Y` 会被 `network X Y` 抢先吃掉。
# 引擎命中第一条即停，不会继续往下找。

def _r(rx, undo=None, view=False, conf="exact", note="", overwrite=False):
    return UndoRule(rx=rx, undo=undo, view=view, conf=conf, note=note,
                    overwrite=overwrite)


def _skip(rx):
    return UndoRule(rx=rx, undo=None, conf="skip")


# ---------------------------------------------------------------- H3C

_H3C = (
    # 会话 / 激活动作：不产生回滚项
    _skip(r"^system-view$"),
    _skip(r"^return$"),
    _skip(r"^quit$"),
    _skip(r"^save( force)?$"),
    _skip(r"^active region-configuration$"),
    _skip(r"^irf-port-configuration active$"),

    # ---- 视图容器 ----
    _r(r"^vlan (\d+) to (\d+)$", "undo vlan {1} to {2}", view=True),
    _r(r"^vlan (\d+)$", "undo vlan {1}", view=True),
    _r(r"^interface range (.+)$", "", view=True),
    _r(r"^interface (.+)$", "", view=True),
    _r(r"^acl (advanced|basic) (\S+)$", "undo acl {1} {2}", view=True),
    _r(r"^ospf (\d+)(?: router-id \S+)?$", "undo ospf {1}", view=True),
    _r(r"^bgp (\d+)$", "undo bgp {1}", view=True),
    _r(r"^isis (\d+)$", "undo isis {1}", view=True),
    _r(r"^domain (\S+)$", "undo domain {1}", view=True),
    _r(r"^radius scheme (\S+)$", "undo radius scheme {1}", view=True),
    _r(r"^dhcp server ip-pool (\S+)$", "undo dhcp server ip-pool {1}", view=True),
    _r(r"^ip vpn-instance (\S+)$", "undo ip vpn-instance {1}", view=True),
    _r(r"^mpls ldp$", "undo mpls ldp", view=True),
    _r(r"^port-isolate group (\d+)$", "undo port-isolate group {1}", view=True),
    _r(r"^mirroring-group (\d+) local$", "undo mirroring-group {1}", view=True),
    _r(r"^stp region-configuration$", "undo stp region-configuration", view=True,
       conf="approx", note="会清空整个 MST 域配置，确认其他实例不需要保留"),
    _r(r"^line vty (.+)$", "", view=True),
    _r(r"^local-user (\S+) class (\S+)$", "undo local-user {1}", view=True),
    _r(r"^irf-port (.+)$", "", view=True),
    _r(r"^address-family (\S+)(?: \S+)?$", "", view=True),
    _r(r"^policy (\S+)$", "", view=True),
    _r(r"^class (\S+)$", "", view=True),
    _r(r"^user-role (\S+)$", "", view=True),

    # ---- 二层 ----
    _r(r"^name (\S+)$", "undo name"),
    _r(r"^port link-type (access|trunk|hybrid)$", "undo port link-type",
       conf="approx", note="撤销后端口回到默认 access 模式", overwrite=True),
    _r(r"^port access vlan (\d+)$", "undo port access vlan",
       conf="approx", note="撤销后端口回到 VLAN 1", overwrite=True),
    _r(r"^port trunk permit vlan (.+)$", "undo port trunk permit vlan {1}"),
    _r(r"^port trunk pvid vlan (\d+)$", "undo port trunk pvid vlan", overwrite=True),
    _r(r"^port link-aggregation group (\d+)$", "undo port link-aggregation group"),
    _r(r"^link-aggregation mode dynamic$", "undo link-aggregation mode"),
    _r(r"^port group interface (\S+)$", "undo port group interface {1}"),
    _r(r"^shutdown$", "undo shutdown"),
    _r(r"^undo shutdown$", "shutdown"),
    _r(r"^stp edged-port$", "undo stp edged-port"),
    _r(r"^stp bpdu-protection$", "undo stp bpdu-protection"),
    _r(r"^stp global enable$", "undo stp global enable"),
    _r(r"^stp mode (\S+)$", "undo stp mode", conf="approx",
       note="撤销后回到设备默认生成树模式", overwrite=True),
    _r(r"^stp instance (\d+) root (primary|secondary)$", "undo stp instance {1} root", overwrite=True),
    _r(r"^stp root (primary|secondary)$", "undo stp root", overwrite=True),
    _r(r"^instance (\d+) vlan (.+)$", "undo instance {1}"),
    _r(r"^region-name (\S+)$", "undo region-name"),
    _r(r"^revision-level (\d+)$", "undo revision-level"),
    _r(r"^port-isolate enable group (\d+)$", "undo port-isolate enable"),
    _r(r"^port-isolate mode (\S+)$", "undo port-isolate mode"),
    _r(r"^loopback-detection global enable (.+)$", "undo loopback-detection global enable"),
    _r(r"^loopback-detection global action (\S+)$", "undo loopback-detection global action"),
    _r(r"^loopback-detection interval-time (\d+)$", "undo loopback-detection interval-time"),
    _r(r"^silent-interface (.+)$", "undo silent-interface {1}"),
    _r(r"^igmp-snooping enable$", "undo igmp-snooping enable"),
    _r(r"^igmp-snooping fast-leave$", "undo igmp-snooping fast-leave"),
    _r(r"^igmp-snooping querier source-address (\S+)$",
       "undo igmp-snooping querier source-address"),
    _r(r"^igmp-snooping querier enable$", "undo igmp-snooping querier"),
    _r(r"^igmp-snooping router-port interface (.+)$", "undo igmp-snooping router-port"),
    _r(r"^igmp-snooping$", "undo igmp-snooping"),
    _r(r"^mirroring-group (\d+) mirroring-port (.+)$",
       "undo mirroring-group {1} mirroring-port"),
    _r(r"^mirroring-group (\d+) monitor-port$", "undo mirroring-group {1} monitor-port"),

    # ---- 安全 ----
    _r(r"^port-security enable$", "undo port-security enable"),
    _r(r"^port-security port-mode (\S+)$", "undo port-security port-mode", overwrite=True),
    _r(r"^port-security max-mac-count (\d+)$", "undo port-security max-mac-count"),
    _r(r"^port-security intrusion-mode (\S+)$", "undo port-security intrusion-mode"),
    _r(r"^port-security timer disableport (\d+)$", "undo port-security timer disableport"),
    _r(r"^broadcast-suppression (.+)$", "undo broadcast-suppression"),
    _r(r"^multicast-suppression (.+)$", "undo multicast-suppression"),
    _r(r"^unicast-suppression (.+)$", "undo unicast-suppression"),
    _r(r"^dhcp snooping enable$", "undo dhcp snooping enable"),
    _r(r"^dhcp snooping trust$", "undo dhcp snooping trust"),
    _r(r"^dhcp snooping check mac-address$", "undo dhcp snooping check mac-address"),
    _r(r"^arp detection enable$", "undo arp detection enable"),
    _r(r"^arp detection trust$", "undo arp detection trust"),
    _r(r"^arp detection validate (.+)$", "undo arp detection validate"),
    _r(r"^ip verify source (.+)$", "undo ip verify source"),
    _r(r"^dot1x port-control (\S+)$", "undo dot1x port-control", overwrite=True),
    _r(r"^dot1x port-method (\S+)$", "undo dot1x port-method", overwrite=True),
    _r(r"^dot1x mac-bypass$", "undo dot1x mac-bypass"),
    _r(r"^dot1x$", "undo dot1x"),
    _r(r"^authentication-mode (\S+)$", "undo authentication-mode"),
    _r(r"^authorization-attribute user-role (\S+)$",
       "undo authorization-attribute user-role"),
    _r(r"^service-type (.+)$", "undo service-type"),
    _r(r"^password (simple|hash) (.+)$", "undo password"),
    _r(r"^protocol inbound (\S+)$", "undo protocol inbound"),
    _r(r"^rule (\d+) (.+)$", "undo rule {1}"),
    _r(r"^step (\d+)$", "undo step"),
    _r(r"^packet-filter (\S+) (inbound|outbound)$", "undo packet-filter {1} {2}"),
    _r(r"^authentication lan-access radius-scheme (\S+)$", "undo authentication lan-access"),
    _r(r"^authorization lan-access radius-scheme (\S+)$", "undo authorization lan-access"),
    _r(r"^key authentication (simple|cipher) (\S+)$", "undo key authentication"),
    _r(r"^primary authentication (\S+)$", "undo primary authentication"),
    _r(r"^user-name-format (\S+)$", "undo user-name-format"),

    # ---- 三层 ----
    _r(r"^ip address (\S+) (\S+)$", "undo ip address {1} {2}", overwrite=True),
    _r(r"^ipv6 address (\S+)( \S+)?$", "undo ipv6 address {1} {2}", overwrite=True),
    _r(r"^ip binding vpn-instance (\S+)$", "undo ip binding vpn-instance"),
    _r(r"^ip route-static (.+)$", "undo ip route-static {1}"),
    _r(r"^dhcp enable$", "undo dhcp enable"),
    _r(r"^dhcp select relay$", "undo dhcp select"),
    _r(r"^dhcp server apply ip-pool (\S+)$", "undo dhcp server apply ip-pool"),
    _r(r"^dhcp relay server-address (\S+)$", "undo dhcp relay server-address {1}"),
    _r(r"^forbidden-ip (.+)$", "undo forbidden-ip {1}"),
    _r(r"^gateway-list (\S+)$", "undo gateway-list {1}"),
    _r(r"^dns-list (\S+)$", "undo dns-list {1}"),
    _r(r"^network (\S+) mask (\S+)$", "undo network {1} mask {2}"),
    _r(r"^network (\S+) (\S+)$", "undo network {1} {2}"),
    _r(r"^vrrp vrid (\d+) virtual-ip (\S+)$", "undo vrrp vrid {1} virtual-ip {2}"),
    _r(r"^vrrp vrid (\d+) priority (\d+)$", "undo vrrp vrid {1} priority"),
    _r(r"^vrrp vrid (\d+) preempt-mode (.+)$", "undo vrrp vrid {1} preempt-mode"),
    _r(r"^router-id (\S+)$", "undo router-id"),
    _r(r"^area (\S+)$", "undo area {1}"),
    _r(r"^import-route (\S+)$", "undo import-route {1}"),
    _r(r"^cost-style (\S+)$", "undo cost-style"),
    _r(r"^is-level (\S+)$", "undo is-level"),
    _r(r"^network-entity (\S+)$", "undo network-entity {1}"),
    _r(r"^isis enable (\d+)$", "undo isis enable {1}"),
    _r(r"^isis circuit-level (\S+)$", "undo isis circuit-level"),
    _r(r"^isis authentication-mode (\S+) (\S+) (\S+)$", "undo isis authentication-mode"),
    _r(r"^peer (\S+) as-number (\d+)$", "undo peer {1}"),
    _r(r"^peer (\S+) enable$", "undo peer {1} enable"),
    _r(r"^peer (\S+) password (.+)$", "undo peer {1} password"),
    _r(r"^peer (\S+) connect-interface (\S+)$", "undo peer {1} connect-interface"),
    _r(r"^peer (\S+) next-hop-local$", "undo peer {1} next-hop-local"),
    _r(r"^route-distinguisher (\S+)$", "undo route-distinguisher"),
    _r(r"^vpn-target (.+)$", None, conf="manual",
       note="vpn-target 的撤销要带方向与 RT 值，请对照现网配置手工处理"),
    _r(r"^mpls enable$", "undo mpls enable"),
    _r(r"^mpls ldp enable$", "undo mpls ldp enable"),
    _r(r"^mpls lsr-id (\S+)$", "undo mpls lsr-id"),

    # ---- 运维 ----
    _r(r"^sysname (\S+)$", "undo sysname", overwrite=True),
    _r(r"^clock timezone (.+)$", "undo clock timezone", overwrite=True),
    _r(r"^ntp-service enable$", "undo ntp-service enable"),
    _r(r"^ntp-service unicast-server (\S+)$", "undo ntp-service unicast-server {1}"),
    _r(r"^info-center enable$", "undo info-center enable"),
    _r(r"^info-center loghost (\S+)$", "undo info-center loghost {1}"),
    _r(r"^info-center logbuffer size (\d+)$", "undo info-center logbuffer size"),
    _r(r"^info-center format (\S+)$", "undo info-center format"),
    _r(r"^info-center source (\S+) (\S+) (.+)$", "undo info-center source {1} {2}"),
    _r(r"^info-center timestamp (\S+) (\S+)$", "undo info-center timestamp {1}"),
    _r(r"^snmp-agent community (read|write) (simple|cipher) (\S+)$",
       "undo snmp-agent community {1} {3}"),
    _r(r"^snmp-agent community (read|write) (\S+)$", "undo snmp-agent community {1} {2}"),
    _r(r"^snmp-agent sys-info version (.+)$", "undo snmp-agent sys-info version {1}", overwrite=True),
    _r(r"^snmp-agent group v3 (\S+) (\S+)$", "undo snmp-agent group v3 {1}"),
    _r(r"^snmp-agent usm-user v3 (\S+)( .+)?$", "undo snmp-agent usm-user v3 {1}"),
    _r(r"^snmp-agent target-host (.+)$", None, conf="manual",
       note="target-host 参数较长，请对照 display snmp-agent target-host 手工撤销"),
    _r(r"^snmp-agent$", "undo snmp-agent"),
    _r(r"^ssh server enable$", "undo ssh server enable"),
    _r(r"^telnet server enable$", "undo telnet server enable"),
    _r(r"^undo telnet server enable$", "telnet server enable"),
    _r(r"^irf domain (\d+)$", "undo irf domain"),
    _r(r"^irf member (\d+) priority (\d+)$", "undo irf member {1} priority"),
    _r(r"^qos lr (inbound|outbound) cir (\d+)$", "undo qos lr {1}"),
    _r(r"^qos trust (\S+)$", "undo qos trust", overwrite=True),
    _r(r"^poe enable$", "undo poe enable"),
    _r(r"^undo poe enable$", "poe enable"),
    _r(r"^poe priority (\S+)$", "undo poe priority", overwrite=True),
    _r(r"^undo ipv6 nd ra halt$", "ipv6 nd ra halt"),

    # 兜底：生成器主动下发的 undo 命令（关闭某项功能），原值不可知，
    # 只能提示人工确认 —— 上面的显式规则会先命中，不会走到这里。
    _r(r"^undo (.+)$", None, conf="manual",
       note="这是一条主动关闭某项功能的命令，恢复原配置需人工确认"),
)


# ---------------------------------------------------------------- 华为

_HUAWEI = (
    _skip(r"^system-view$"),
    _skip(r"^return$"),
    _skip(r"^quit$"),
    _skip(r"^save$"),
    _skip(r"^active region-configuration$"),

    # ---- 视图容器 ----
    _r(r"^vlan batch (.+)$", "undo vlan batch {1}"),
    _r(r"^vlan (\d+)$", "undo vlan {1}", view=True),
    _r(r"^interface range (.+)$", "", view=True),
    _r(r"^interface (.+)$", "", view=True),
    _r(r"^acl (number|name) (\S+)$", "undo acl {1} {2}", view=True),
    _r(r"^ospf (\d+)(?: router-id \S+)?$", "undo ospf {1}", view=True),
    _r(r"^bgp (\d+)$", "undo bgp {1}", view=True),
    _r(r"^isis (\d+)$", "undo isis {1}", view=True),
    _r(r"^ip vpn-instance (\S+)$", "undo ip vpn-instance {1}", view=True),
    _r(r"^ip pool (\S+)$", "undo ip pool {1}", view=True),
    _r(r"^aaa$", "undo aaa", view=True),
    _r(r"^authentication-scheme (\S+)$", "undo authentication-scheme {1}", view=True),
    _r(r"^radius-server template (\S+)$", "undo radius-server template {1}", view=True),
    _r(r"^radius-server (\S+)$", "undo radius-server {1}", view=True),
    _r(r"^domain (\S+)$", "undo domain {1}", view=True),
    _r(r"^address-family (ipv4|ipv6)$", "undo address-family {1}", view=True),
    _r(r"^user-interface (.+)$", "", view=True),
    _r(r"^local-user (\S+) password (irreversible-)?cipher (.+)$", "undo local-user {1}"),
    _r(r"^local-user (\S+) (.+)$", "undo local-user {1}"),
    _r(r"^eth-trunk (\d+)$", "", view=True),
    _r(r"^observe-port (\d+) interface (.+)$", "undo observe-port {1}", view=True),
    _r(r"^stp region-configuration$", "undo stp region-configuration", view=True,
       conf="approx", note="会清空整个 MST 域配置"),
    _r(r"^ipv4-family( .+)?$", "", view=True),
    _r(r"^mpls ldp$", "undo mpls ldp", view=True),

    # ---- 二层 ----
    _r(r"^description (.+)$", "undo description", overwrite=True),
    _r(r"^port link-type (access|trunk|hybrid)$", "undo port link-type",
       conf="approx", note="撤销后端口回到默认 access 模式", overwrite=True),
    _r(r"^port default vlan (\d+)$", "undo port default vlan",
       conf="approx", note="撤销后端口回到 VLAN 1", overwrite=True),
    _r(r"^port trunk allow-pass vlan (.+)$", "undo port trunk allow-pass vlan {1}"),
    _r(r"^port trunk pvid vlan (\d+)$", "undo port trunk pvid vlan", overwrite=True),
    _r(r"^mode lacp-static$", "undo mode", conf="approx", note="聚合模式回到默认手工模式"),
    _r(r"^mode manual load-balance$", "undo mode", conf="approx",
       note="聚合模式回到默认手工模式"),
    _r(r"^shutdown$", "undo shutdown"),
    _r(r"^undo shutdown$", "shutdown"),
    _r(r"^stp edged-port enable$", "undo stp edged-port"),
    _r(r"^stp bpdu-protection$", "undo stp bpdu-protection"),
    _r(r"^stp mode (\S+)$", "undo stp mode", conf="approx", note="回到设备默认生成树模式", overwrite=True),
    _r(r"^stp instance (\d+) root (primary|secondary)$", "undo stp instance {1} root", overwrite=True),
    _r(r"^stp root (primary|secondary)$", "undo stp root", overwrite=True),
    _r(r"^stp global enable$", "undo stp global enable"),
    _r(r"^instance (\d+) vlan (.+)$", "undo instance {1}"),
    _r(r"^region-name (\S+)$", "undo region-name"),
    _r(r"^revision-level (\d+)$", "undo revision-level"),
    _r(r"^igmp-snooping enable$", "undo igmp-snooping enable"),
    _r(r"^igmp-snooping prompt-leave$", "undo igmp-snooping prompt-leave"),
    _r(r"^igmp-snooping querier source-address (\S+)$",
       "undo igmp-snooping querier source-address"),
    _r(r"^igmp-snooping querier enable$", "undo igmp-snooping querier"),
    _r(r"^port-mirroring to observe-port (\d+) (.+)$", "undo port-mirroring"),
    _r(r"^loopback-detect enable$", "undo loopback-detect enable"),
    _r(r"^loopback-detect action (\S+)$", "undo loopback-detect action"),
    _r(r"^loopback-detect interval (\d+)$", "undo loopback-detect interval"),
    _r(r"^port-isolate enable( group \d+)?$", "undo port-isolate enable"),

    # ---- 安全 ----
    _r(r"^port-security enable$", "undo port-security enable"),
    _r(r"^port-security mac-address sticky$", "undo port-security mac-address sticky"),
    _r(r"^port-security max-mac-num (\d+)$", "undo port-security max-mac-num", overwrite=True),
    _r(r"^port-security protect-action (\S+)$", "undo port-security protect-action", overwrite=True),
    _r(r"^storm control (\S+) min-rate (.+)$", "undo storm control {1}"),
    _r(r"^traffic-filter (inbound|outbound) acl (\S+)$", "undo traffic-filter {1}"),
    _r(r"^dhcp snooping enable$", "undo dhcp snooping enable"),
    _r(r"^dhcp snooping trusted$", "undo dhcp snooping trusted"),
    _r(r"^arp anti-attack check user-bind enable$",
       "undo arp anti-attack check user-bind enable"),
    _r(r"^ip source check user-bind enable$", "undo ip source check user-bind enable"),
    _r(r"^dot1x enable$", "undo dot1x enable"),
    _r(r"^dot1x (port-control|port-method|mac-bypass) (.+)$", "undo dot1x {1}"),
    _r(r"^dot1x$", "undo dot1x"),
    _r(r"^authentication-mode (\S+)$", "undo authentication-mode"),
    _r(r"^authentication (\S+) (.+)$", "undo authentication {1}"),
    _r(r"^authorization (\S+) (.+)$", "undo authorization {1}"),
    _r(r"^service-type (.+)$", "undo service-type"),
    _r(r"^privilege level (\d+)$", "undo privilege level"),
    _r(r"^rule (\d+) (.+)$", "undo rule {1}"),
    _r(r"^step (\d+)$", "undo step"),
    _r(r"^radius-server (authentication|accounting) (\S+) (\d+)$",
       "undo radius-server {1} {2} {3}"),
    _r(r"^radius-server shared-key (cipher )?(\S+)$", "undo radius-server shared-key"),

    # ---- 三层 ----
    _r(r"^ip address (\S+) (\S+)$", "undo ip address {1} {2}", overwrite=True),
    _r(r"^ipv6 address (\S+)( \S+)?$", "undo ipv6 address {1} {2}", overwrite=True),
    _r(r"^ipv6 enable$", "undo ipv6 enable"),
    _r(r"^ip binding vpn-instance (\S+)$", "undo ip binding vpn-instance"),
    _r(r"^ip route-static (.+)$", "undo ip route-static {1}"),
    _r(r"^dhcp enable$", "undo dhcp enable"),
    _r(r"^dhcp select (global|relay|interface)$", "undo dhcp select"),
    _r(r"^dhcp relay server-ip (\S+)$", "undo dhcp relay server-ip {1}"),
    _r(r"^dhcp server excluded-ip-address (.+)$", "undo dhcp server excluded-ip-address"),
    _r(r"^excluded-ip-address (.+)$", "undo excluded-ip-address {1}"),
    _r(r"^gateway-list (\S+)$", "undo gateway-list {1}"),
    _r(r"^dns-list (\S+)$", "undo dns-list {1}"),
    _r(r"^network (\S+) mask (\S+)$", "undo network {1} mask {2}"),
    _r(r"^network (\S+) (\S+)$", "undo network {1} {2}"),
    _r(r"^vrrp vrid (\d+) virtual-ip (\S+)$", "undo vrrp vrid {1} virtual-ip {2}"),
    _r(r"^vrrp vrid (\d+) priority (\d+)$", "undo vrrp vrid {1} priority"),
    _r(r"^vrrp vrid (\d+) preempt-mode (.+)$", "undo vrrp vrid {1} preempt-mode"),
    _r(r"^router-id (\S+)$", "undo router-id"),
    _r(r"^area (\S+)$", "undo area {1}"),
    _r(r"^silent-interface (.+)$", "undo silent-interface {1}"),
    _r(r"^import-route (\S+)$", "undo import-route {1}"),
    _r(r"^cost-style (\S+)$", "undo cost-style"),
    _r(r"^is-level (\S+)$", "undo is-level"),
    _r(r"^network-entity (\S+)$", "undo network-entity {1}"),
    _r(r"^isis circuit-level (\S+)$", "undo isis circuit-level"),
    _r(r"^isis enable (\d+)$", "undo isis enable {1}"),
    _r(r"^isis authentication-mode (\S+) (\S+) (\S+)$", "undo isis authentication-mode"),
    _r(r"^peer (\S+) as-number (\d+)$", "undo peer {1}"),
    _r(r"^peer (\S+) enable$", "undo peer {1} enable"),
    _r(r"^peer (\S+) password (.+)$", "undo peer {1} password"),
    _r(r"^peer (\S+) connect-interface (\S+)$", "undo peer {1} connect-interface"),
    _r(r"^peer (\S+) next-hop-local$", "undo peer {1} next-hop-local"),
    _r(r"^route-distinguisher (\S+)$", "undo route-distinguisher"),
    _r(r"^vpn-target (.+)$", None, conf="manual",
       note="vpn-target 的撤销要带方向与 RT 值，请手工处理"),
    _r(r"^policy vpn-target$", "undo policy vpn-target"),
    _r(r"^mpls$", "undo mpls"),
    _r(r"^mpls lsr-id (\S+)$", "undo mpls lsr-id"),

    # ---- 运维 ----
    _r(r"^sysname (\S+)$", "undo sysname", overwrite=True),
    _r(r"^clock timezone (.+)$", "undo clock timezone", overwrite=True),
    _r(r"^ntp-service enable$", "undo ntp-service enable"),
    _r(r"^ntp-service unicast-server (\S+)$", "undo ntp-service unicast-server {1}"),
    _r(r"^info-center enable$", "undo info-center enable"),
    _r(r"^info-center loghost (\S+)$", "undo info-center loghost {1}"),
    _r(r"^info-center logbuffer size (\d+)$", "undo info-center logbuffer size"),
    _r(r"^info-center source (\S+) (\S+) (.+)$", "undo info-center source {1} {2}"),
    _r(r"^snmp-agent community (read|write) (cipher|simple) (\S+)$",
       "undo snmp-agent community {1} {3}"),
    _r(r"^snmp-agent community (read|write) (\S+)$", "undo snmp-agent community {1} {2}"),
    _r(r"^snmp-agent sys-info version (.+)$", "undo snmp-agent sys-info version {1}", overwrite=True),
    _r(r"^snmp-agent group v3 (\S+) (\S+)$", "undo snmp-agent group v3 {1}"),
    _r(r"^snmp-agent usm-user v3 (\S+)( .+)?$", "undo snmp-agent usm-user v3 {1}"),
    _r(r"^snmp-agent target-host (.+)$", None, conf="manual",
       note="target-host 参数较长，请手工撤销"),
    _r(r"^snmp-agent$", "undo snmp-agent"),
    _r(r"^stelnet server enable$", "undo stelnet server enable"),
    _r(r"^ssh server enable$", "undo ssh server enable"),
    _r(r"^telnet server enable$", "undo telnet server enable"),
    _r(r"^qos car (inbound|outbound) cir (\d+)$", "undo qos car {1}"),
    _r(r"^qos lr (inbound|outbound) cir (\d+)$", "undo qos lr {1}"),
    _r(r"^qos trust (\S+)$", "undo qos trust", overwrite=True),
    _r(r"^poe enable$", "undo poe enable"),
    _r(r"^poe priority (\S+)$", "undo poe priority", overwrite=True),

    # 华为特有的零散命令（与 H3C 写法不同，必须单独列）
    _r(r"^protocol inbound (\S+)$", "undo protocol inbound"),
    _r(r"^trust (\S+)( .+)?$", "undo trust"),
    _r(r"^port interface (\S+) enable$", "undo port interface {1}"),
    _r(r"^stack slot (\d+) priority (\d+)$", "undo stack slot {1} priority"),
    _r(r"^stack slot (\d+)$", "", view=True),
    _r(r"^storm control action (\S+)$", "undo storm control action"),
    _r(r"^stp enable$", "undo stp enable"),
    _r(r"^info-center console channel (.+)$", "undo info-center console"),
    _r(r"^info-center timestamp (\S+) (\S+)$", "undo info-center timestamp {1}"),
    _r(r"^loop-detection enable( vlan .+)?$", "undo loop-detection enable"),
    _r(r"^loop-detection interval-time (\d+)$", "undo loop-detection interval-time"),
    _r(r"^igmp-snooping router-port interface (.+)$", "undo igmp-snooping router-port"),
    _r(r"^rsa local-key-pair create$", None, conf="manual",
       note="生成 RSA 密钥的逆操作是 rsa local-key-pair destroy，会清空已有密钥，"
            "确认 SSH 不再使用后再执行"),
    _r(r"^undo telnet server enable$", "telnet server enable"),
    _r(r"^undo poe enable$", "poe enable"),
    _r(r"^undo ipv6 nd ra halt$", "ipv6 nd ra halt"),

    # 兜底：主动下发的 undo 命令，原值不可知
    _r(r"^undo (.+)$", None, conf="manual",
       note="这是一条主动关闭某项功能的命令，恢复原配置需人工确认"),
)


# ---------------------------------------------------------------- IOS 系
#
# 思科 / 锐捷的撤销绝大多数就是加 no 前缀，且允许带完整参数，所以这里只显式
# 列出「不能简单加 no」的：需要截断尾部参数、需要换成另一条命令、或者撤销后
# 语义会变的。其余交给末尾的兜底规则，统一标 approx。

_IOS_COMMON = (
    _skip(r"^configure terminal$"),
    _skip(r"^conf t$"),
    _skip(r"^end$"),
    _skip(r"^exit$"),
    _skip(r"^exit-address-family$"),
    _skip(r"^write( memory)?$"),

    # ---- 视图容器 ----
    _r(r"^vlan (\d+)$", "no vlan {1}", view=True),
    _r(r"^interface range (.+)$", "", view=True),
    _r(r"^interface (.+)$", "", view=True),
    _r(r"^router ospf (\d+)$", "no router ospf {1}", view=True),
    _r(r"^router bgp (\d+)$", "no router bgp {1}", view=True),
    _r(r"^router isis( \S+)?$", "no router isis", view=True),
    _r(r"^ip access-list (extended|standard) (\S+)$",
       "no ip access-list {1} {2}", view=True),
    _r(r"^ip dhcp pool (\S+)$", "no ip dhcp pool {1}", view=True),
    _r(r"^ip vrf (\S+)$", "no ip vrf {1}", view=True),
    _r(r"^key chain (\S+)$", "no key chain {1}", view=True),
    _r(r"^policy-map (\S+)$", "no policy-map {1}", view=True),
    _r(r"^class (\S+)$", "", view=True),
    _r(r"^line vty (.+)$", "", view=True),
    _r(r"^address-family (.+)$", "", view=True),
    _r(r"^radius server (\S+)$", "no radius server {1}", view=True),
    _r(r"^switch (\d+)$", "", view=True),
    _r(r"^vsl-port$", "", view=True),

    # ---- 需要截断尾部参数 ----
    _r(r"^(\d+) (permit|deny) (.+)$", "no {1}"),
    _r(r"^hostname (\S+)$", "no hostname", overwrite=True),
    _r(r"^username (\S+) (.+)$", "no username {1}"),
    _r(r"^snmp-server community (\S+)( .+)?$", "no snmp-server community {1}"),
    _r(r"^snmp-server group (\S+)( .+)?$", "no snmp-server group {1}"),
    _r(r"^snmp-server user (\S+) (\S+)( .+)?$", "no snmp-server user {1} {2}"),
    _r(r"^snmp-server host (\S+)( .+)?$", "no snmp-server host {1}"),
    _r(r"^snmp-server location (.+)$", "no snmp-server location", overwrite=True),
    _r(r"^snmp-server contact (.+)$", "no snmp-server contact"),
    _r(r"^snmp-server enable traps( .+)?$", "no snmp-server enable traps"),
    _r(r"^logging (host|server) (\S+)( .+)?$", "no logging {1} {2}"),
    _r(r"^logging buffered (\d+)$", "no logging buffered"),
    _r(r"^logging trap (\S+)$", "no logging trap", overwrite=True),
    _r(r"^logging console$", "no logging console"),
    _r(r"^logging source-interface (\S+)$", "no logging source-interface"),
    _r(r"^logging on$", "no logging on"),
    _r(r"^ntp server (\S+)( .+)?$", "no ntp server {1}"),
    _r(r"^clock timezone (.+)$", "no clock timezone", overwrite=True),
    _r(r"^service timestamps (.+)$", "no service timestamps"),
    _r(r"^service password-encryption$", "no service password-encryption"),
    _r(r"^ip ssh version (\d+)$", "no ip ssh version"),
    _r(r"^ip domain-name (\S+)$", "no ip domain-name"),
    _r(r"^ip default-gateway (\S+)$", "no ip default-gateway", overwrite=True),
    _r(r"^ip route vrf (.+)$", "no ip route vrf {1}"),
    _r(r"^ip route (.+)$", "no ip route {1}"),
    _r(r"^ipv6 route (.+)$", "no ipv6 route {1}"),
    _r(r"^ip address (\S+) (\S+)$", "no ip address {1} {2}", overwrite=True),
    _r(r"^ipv6 address (\S+)$", "no ipv6 address {1}", overwrite=True),
    _r(r"^ip helper-address (\S+)$", "no ip helper-address {1}"),
    _r(r"^ip vrf forwarding (\S+)$", "no ip vrf forwarding"),
    _r(r"^ip dhcp excluded-address (.+)$", "no ip dhcp excluded-address {1}"),
    _r(r"^ip igmp snooping vlan (\d+)( .+)?$", "no ip igmp snooping vlan {1} {2}"),
    _r(r"^ip access-group (\S+) (in|out)$", "no ip access-group {1} {2}"),
    _r(r"^monitor session (\d+) (source|destination) (.+)$",
       "no monitor session {1} {2} {3}"),
    _r(r"^errdisable recovery cause (\S+)$", "no errdisable recovery cause {1}"),
    _r(r"^errdisable recovery interval (\d+)$", "no errdisable recovery interval"),
    _r(r"^aaa (authentication|authorization) (.+)$", "no aaa {1} {2}"),
    _r(r"^address ipv4 (\S+)( .+)?$", "no address ipv4 {1}"),
    _r(r"^key (string )?(\S+)$", "no key"),
    _r(r"^key (\d+)$", "no key {1}"),
    _r(r"^transport input (.+)$", "no transport input"),
    _r(r"^exec-timeout (.+)$", "no exec-timeout"),
    _r(r"^network (\S+) mask (\S+)$", "no network {1} mask {2}"),
    _r(r"^network (\S+) (\S+)$", "no network {1} {2}"),
    _r(r"^default-router (\S+)$", "no default-router {1}"),
    _r(r"^dns-server (.+)$", "no dns-server"),
    _r(r"^vrrp (\d+) ip (\S+)$", "no vrrp {1} ip {2}"),
    _r(r"^vrrp (\d+) priority (\d+)$", "no vrrp {1} priority", overwrite=True),
    _r(r"^vrrp (\d+) preempt$", "no vrrp {1} preempt"),
    _r(r"^standby (\d+) ip (\S+)$", "no standby {1} ip {2}"),
    _r(r"^standby (\d+) priority (\d+)$", "no standby {1} priority", overwrite=True),
    _r(r"^standby (\d+) preempt(.+)?$", "no standby {1} preempt"),
    _r(r"^area (\S+)( .+)?$", "no area {1}"),
    _r(r"^redistribute (\S+)( .+)?$", "no redistribute {1}"),
    _r(r"^passive-interface (.+)$", "no passive-interface {1}"),
    _r(r"^neighbor (\S+) remote-as (\d+)$", "no neighbor {1} remote-as {2}"),
    _r(r"^neighbor (\S+) (.+)$", "no neighbor {1} {2}"),
    _r(r"^bgp router-id (\S+)$", "no bgp router-id"),
    _r(r"^bgp log-neighbor-changes$", "no bgp log-neighbor-changes"),
    _r(r"^no bgp default ipv4-unicast$", "bgp default ipv4-unicast"),
    _r(r"^rd (\S+)$", "no rd {1}"),
    _r(r"^route-target (import|export) (\S+)$", "no route-target {1} {2}"),
    _r(r"^service-policy (inbound|outbound) (\S+)$", "no service-policy {1} {2}"),
    _r(r"^port-member interface (.+)$", "no port-member interface {1}"),
    _r(r"^port interface (.+)$", "no port interface {1}"),
    _r(r"^switch (\d+) priority (\d+)$", "no switch {1} priority"),
    _r(r"^ldp router-id (.+)$", "no ldp router-id"),
    _r(r"^mpls ldp router-id (.+)$", "no mpls ldp router-id"),
    _r(r"^spanning-tree mst (\d+) root (primary|secondary)$",
       "no spanning-tree mst {1} root"),
    _r(r"^instance (\d+) vlan (.+)$", "no instance {1}"),
    _r(r"^storm-control (\S+) (level|pps) (.+)$", "no storm-control {1}"),
    _r(r"^switchport trunk allowed vlan( add| remove| all| none| except)? (.+)$",
       "no switchport trunk allowed vlan {2}"),
    _r(r"^switchport trunk native vlan (\d+)$", "no switchport trunk native vlan", overwrite=True),
    _r(r"^switchport trunk encapsulation (\S+)$", "no switchport trunk encapsulation"),
    _r(r"^switchport port-security mac-address sticky$",
       "no switchport port-security mac-address sticky"),
    _r(r"^channel-group (\d+) mode (.+)$", "no channel-group", conf="approx",
       note="撤销后成员口脱离聚合组"),
    _r(r"^port-group (\d+) mode (.+)$", "no port-group", conf="approx",
       note="撤销后成员口脱离聚合组"),

    # ---- 撤销后语义会变 / 需要整块关掉 ----
    _r(r"^switchport mode (access|trunk)$", "no switchport mode",
       conf="approx", note="撤销后端口回到设备默认模式", overwrite=True),
    _r(r"^switchport access vlan (\d+)$", "no switchport access vlan",
       conf="approx", note="撤销后端口回到 VLAN 1", overwrite=True),
    _r(r"^switchport port-security( .+)?$", "no switchport port-security",
       conf="approx", note="整块关闭端口安全，已学到的 sticky MAC 一并清除"),
    _r(r"^switchport protected$", "no switchport protected"),
    _r(r"^shutdown$", "no shutdown"),
    _r(r"^no shutdown$", "shutdown"),
    _r(r"^spanning-tree mode (\S+)$", "no spanning-tree mode", conf="approx",
       note="撤销后回到设备默认生成树模式", overwrite=True),
    _r(r"^spanning-tree portfast$", "no spanning-tree portfast"),
    _r(r"^spanning-tree portfast (bpduguard|bpdufilter) default$",
       "no spanning-tree portfast {1} default"),
    _r(r"^spanning-tree bpduguard enable$", "no spanning-tree bpduguard enable"),
    _r(r"^spanning-tree bpdufilter enable$", "no spanning-tree bpdufilter enable"),
    _r(r"^spanning-tree mst configuration$", "no spanning-tree mst configuration",
       view=True, conf="approx", note="会清空整个 MST 域配置"),
    _r(r"^name (\S+)$", "no name"),
    _r(r"^revision (\d+)$", "no revision"),
    _r(r"^ip dhcp snooping$", "no ip dhcp snooping"),
    _r(r"^ip dhcp snooping vlan (.+)$", "no ip dhcp snooping vlan {1}"),
    _r(r"^ip dhcp snooping trust$", "no ip dhcp snooping trust"),
    _r(r"^ip dhcp snooping database (.+)$", "no ip dhcp snooping database"),
    _r(r"^no ip dhcp snooping information option$", "ip dhcp snooping information option"),
    _r(r"^ip arp inspection vlan (.+)$", "no ip arp inspection vlan {1}"),
    _r(r"^ip arp inspection validate (.+)$", "no ip arp inspection validate"),
    _r(r"^ip arp inspection trust$", "no ip arp inspection trust"),
    _r(r"^ip verify source( .+)?$", "no ip verify source"),
    _r(r"^ip igmp snooping$", "no ip igmp snooping"),
    _r(r"^mls qos$", "no mls qos"),
    _r(r"^mls qos trust (\S+)$", "no mls qos trust"),
    _r(r"^no mls qos trust$", "mls qos trust cos"),
    _r(r"^aaa new-model$", "no aaa new-model"),
    _r(r"^dot1x system-auth-control$", "no dot1x system-auth-control"),
    _r(r"^dot1x authentication default group radius$",
       "no dot1x authentication default group radius"),
    _r(r"^dot1x (pae|host-mode) (\S+)$", "no dot1x {1}"),
    _r(r"^authentication port-control (\S+)$", "no authentication port-control"),
    _r(r"^login local$", "no login local"),
    _r(r"^enable service ssh-server$", "no enable service ssh-server"),
    _r(r"^crypto key generate rsa( modulus \d+)?$", "crypto key zeroize rsa",
       conf="approx", note="会清除设备上全部 RSA 密钥，之后 SSH 需重新生成"),
    _r(r"^ip routing$", "no ip routing"),
    _r(r"^ip cef$", "no ip cef"),
    _r(r"^ipv6 unicast-routing$", "no ipv6 unicast-routing"),
    _r(r"^ipv6 cef$", "no ipv6 cef"),
    _r(r"^ipv6 enable$", "no ipv6 enable"),
    _r(r"^ipv6 nd ra suppress$", "no ipv6 nd ra suppress"),
    _r(r"^router-id (\S+)$", "no router-id"),
    _r(r"^metric-style (\S+)$", "no metric-style"),
    _r(r"^is-type (\S+)$", "no is-type"),
    _r(r"^net (\S+)$", "no net {1}"),
    _r(r"^ip router isis( \S+)?$", "no ip router isis"),
    _r(r"^isis (authentication|circuit-type) (.+)$", "no isis {1}"),
    _r(r"^police (.+)$", None, conf="manual",
       note="class 内的限速策略需连同 policy-map 一起处理"),
    _r(r"^mpls ip$", "no mpls ip"),
    _r(r"^mpls label protocol (\S+)$", "no mpls label protocol"),
    _r(r"^label-switch$", "no label-switch"),
    _r(r"^power inline (auto|never)$", "no power inline", conf="approx",
       note="撤销后端口回到设备默认供电策略", overwrite=True),
    _r(r"^poe enable$", "no poe enable"),
    _r(r"^no poe enable$", "poe enable"),
    _r(r"^poe priority (\S+)$", "no poe priority", overwrite=True),

    # 兜底：生成器主动下发的 no 命令（关闭某项功能），恢复成开启即可。
    # 所有「no shutdown」「no poe enable」「no mls qos trust」之类的显式规则
    # 都排在前面，不会走到这里。
    _r(r"^no (.+)$", "{1}", conf="approx",
       note="恢复被主动关闭的功能，原状态需在设备上确认"),
)

#: 兜底：IOS 系绝大多数配置命令加 no 前缀即可撤销
_IOS_FALLBACK = (
    _r(r"^(?!no )([a-z].*)$", "no {1}", conf="approx",
       note="按通用 no 前缀撤销，请在设备上确认结果"),
)

_RUIJIE_EXTRA = (
    _r(r"^switchport trunk allowed vlan all$", "no switchport trunk allowed vlan all"),
    _r(r"^loopback-detection enable$", "no loopback-detection enable"),
    _r(r"^loopback-detection interval (\d+)$", "no loopback-detection interval"),
    _r(r"^loopback-detection control (\S+)$", "no loopback-detection control"),
    _r(r"^storm-control (\S+) pps (\d+)$", "no storm-control {1}"),
    _r(r"^dot1x$", "no dot1x"),
)


_COMPILED = {
    "h3c": _compile(_H3C),
    "huawei": _compile(_HUAWEI),
    "cisco": _compile(_IOS_COMMON + _IOS_FALLBACK),
    "ruijie": _compile(_RUIJIE_EXTRA + _IOS_COMMON + _IOS_FALLBACK),
}
