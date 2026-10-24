"""把多个场景的独立片段，合并成一份能一次粘到底的配置。

为什么需要这一层
----------------
每个场景单独看都是完整的一段：自己进系统视图、配好、退出、保存。
只生成一个场景时没问题，三个场景拼在一起就露馅了——一份配置里出现
八次 ``system-view``、三次 ``save force``，看着像程序在重复输出。
真实下发时这些重复既啰嗦，又容易让人误判（以为要保存三遍）。

处理三件事
----------
1. 「进入系统视图」只留第一个。中间那些进视图命令在删掉退出命令之后
   本来就是多余的 —— 配置流一直待在系统视图里。
2. 「退出 + 保存」只留最后一组。整份配置下发完存一次就够了。
3. 跨场景重复的提示行去重。同一条提示可能被 registry 的场景说明和厂商
   命令模块各写一遍，字面上只差一个空格或斜杠。

第 1 条只对 H3C / 华为 做，思科 / 锐捷 不动
--------------------------------------------
这不是偷懒，是删了会出错：

* H3C 的 ``display``、华为的 ``display`` 在系统视图里照样能执行，
  中间的 ``system-view`` 删掉不影响任何东西。
* 思科 / 锐捷的 ``show`` **必须在特权模式**，中间的 ``end`` 加
  ``configure terminal`` 是成对的 —— 删掉 ``configure terminal`` 会让
  后面的配置命令落在特权模式里直接报错，删掉 ``end`` 又会让 ``show``
  跑到全局配置模式里报错。所以这两家只做第 2、3 条。
"""
from __future__ import annotations

from .vendors import BY_KEY

#: 这些厂商的查看命令在系统视图里能直接跑，所以重复的进入视图命令可以删。
#: 名单之外的（思科 / 锐捷）保持原样，理由见模块文档。
ENTER_SAFE_TO_SLIM = ("h3c", "huawei")


def vendor_cmds(vendor_key: str) -> tuple:
    """取某厂商的「进系统视图」和「退出 + 保存」命令。

    返回 ``(enter, save)``，两者都是元组。``save`` 的最后一条是真正写盘的
    那条（如 ``save force`` / ``write``），前面是退出命令（如 ``return`` / ``end``）。
    """
    module = BY_KEY.get(vendor_key)
    if module is None:
        return (), ()

    enter = getattr(module, "_ENTER", ())
    save = getattr(module, "_SAVE", ())

    if isinstance(enter, str):
        enter = (enter,)
    if isinstance(save, str):
        save = (save,)

    enter = tuple(x for x in enter if x)
    save = tuple(x for x in save if x)
    return enter, save


def item_key(text: str) -> str:
    """提示行的归一化键，用来判断两条是不是同一件事。

    只抹掉空白和斜杠，不动别的字符 —— 宁可漏合并，也不要把两条意思
    近似的提示当成重复删掉，那会丢信息。
    """
    return "".join(str(text).split()).replace("/", "").replace("／", "")


def _tail_start(lines: list, row: int, leave: tuple) -> set:
    """从保存命令那一行往上，找出该一起删掉的行。

    往上只吃三种东西：退出命令、块标题（``# ---- xxx ----``）、空行。
    碰上真正的命令行就停 —— 说明这一段还有别的内容，不能越删越多。
    空行也停，留一个空行当段落间隔，不然删完之后前后两段会黏在一起。
    """
    drop = {row}
    index = row - 1
    while index >= 0:
        text = lines[index].strip()
        if not text:
            break
        if text in leave or text.startswith("# ----"):
            drop.add(index)
            index -= 1
            continue
        break
    return drop


def tidy_commands(lines: list, vendor_key: str) -> list:
    """按厂商整理命令流，去掉重复的进入视图与保存。

    只处理这两种冗余，其余行原样保留，包括注释和空行。只有一段配置
    （或者压根没有保存命令）时原样返回，不碰任何东西。
    """
    enter, save = vendor_cmds(vendor_key)
    if not lines or not save:
        return list(lines)

    last_save = save[-1]
    leave = save[:-1]
    slim_enter = vendor_key in ENTER_SAFE_TO_SLIM

    save_rows = [i for i, line in enumerate(lines) if line.strip() == last_save]
    drop: set = set()
    if len(save_rows) > 1:
        for row in save_rows[:-1]:
            drop |= _tail_start(lines, row, leave)

    out: list = []
    entered = False
    for index, line in enumerate(lines):
        if index in drop:
            continue
        if slim_enter and line.strip() in enter:
            if entered:
                # 前面已经进过系统视图了，这条是多余的
                continue
            entered = True
        out.append(line)

    # 删掉保存块之后容易留下两个挨着的空行，压成一个
    compact: list = []
    for line in out:
        if not line.strip() and compact and not compact[-1].strip():
            continue
        compact.append(line)
    return compact


def dedup_lines(lines: list) -> list:
    """去掉完全重复的提示行，保留第一次出现的那条。"""
    seen: set = set()
    out: list = []
    for line in lines:
        text = line.strip()
        if text.startswith("# [提示]") or text.startswith("# [风险]"):
            key = item_key(text)
            if key in seen:
                continue
            seen.add(key)
        out.append(line)
    return out
