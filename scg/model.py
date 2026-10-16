"""Yang-SwitchKit 核心数据模型。

这里只放"描述性"的数据结构，不含任何厂商命令，保证命令库可以独立演进。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


class Kind:
    """参数控件类型。"""

    TEXT = "text"      # 单行输入
    NUM = "num"        # 数字输入
    CHOICE = "choice"  # 下拉选择
    BOOL = "bool"      # 勾选
    AREA = "area"      # 多行输入


@dataclass(frozen=True)
class Param:
    """一个可编辑参数的定义，界面表单由它动态生成。"""

    key: str
    label: str
    kind: str = Kind.TEXT
    default: str = ""
    choices: Sequence[tuple] = ()
    help: str = ""
    example: str = ""
    span: int = 1  # 表单栅格宽度：1 = 半行，2 = 整行


@dataclass(frozen=True)
class Scenario:
    """一个配置场景（例如 VLAN 划分、链路聚合）。"""

    key: str
    name: str
    group: str
    summary: str
    params: Sequence[Param]
    versions: str = ""
    notes: Sequence[str] = ()
    reboot: bool = False  # 该场景会使设备重启或短暂中断业务

    def param_map(self) -> dict:
        return {p.key: p for p in self.params}


@dataclass(frozen=True)
class Vendor:
    """一个厂商 / 网络操作系统。"""

    key: str
    name: str
    platform: str
    accent: str  # 界面强调色（十六进制）
    port_hint: str = ""  # 接口命名习惯提示，界面会随厂商切换显示


@dataclass
class Block:
    """输出中被分隔符隔开的一段命令。"""

    title: str
    lines: Sequence[str] = ()
    comment: str = ""


@dataclass
class GenResult:
    """一次生成的完整结果。"""

    vendor_name: str = ""
    scenario_name: str = ""
    blocks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_text(self, header: bool = True, comments: bool = True) -> str:
        """渲染为可直接粘贴 / 保存的纯文本。

        ``comments=False`` 时只留命令本身：分节标题、说明、风险与提示，
        以及思科 / 锐捷那种 ``!`` 注释行全部去掉，得到一段可以直接整段
        粘到设备上的命令。``#`` 与 ``!`` 在两家 CLI 里都不会出现在命令
        开头，按首字符过滤是安全的。
        """
        width = 68

        if not comments:
            plain: list[str] = []
            for b in self.blocks:
                plain.extend(_command_lines(b))
            return "\n".join(plain)

        out: list[str] = []
        if header:
            out.append("# " + "=" * width)
            out.append(f"# {self.vendor_name} | {self.scenario_name}")
            out.append("# 由 Yang-SwitchKit 生成，执行前请核对版本与端口名")
            out.append("# " + "=" * width)

        for b in self.blocks:
            out.append("")
            if b.title:
                out.append(f"# ---- {b.title} ----")
            if b.comment:
                for line in b.comment.splitlines():
                    out.append(f"# {line}" if line.strip() else "#")
            out.extend(b.lines)

        tail: list[str] = []
        if self.warnings:
            tail.append("")
            tail.append("# " + "-" * width)
            for w in self.warnings:
                tail.append(f"# [风险] {w}")
        if self.notes:
            tail.append("")
            tail.append("# " + "-" * width)
            for n in self.notes:
                tail.append(f"# [提示] {n}")

        return "\n".join(out + tail)


def _command_lines(block) -> list:
    """取一段里真正的命令行，扔掉空行与注释行（# 和 IOS 风格的 !）。"""
    out: list[str] = []
    for line in block.lines:
        text = str(line).rstrip()
        if not text.strip() or text.lstrip()[:1] in ("#", "!"):
            continue
        out.append(text)
    return out
