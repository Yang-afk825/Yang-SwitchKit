"""方案文件：把一整屏填好的参数存成 .json，下次打开接着改。

为什么单独放一个模块
-------------------
只存数据，不碰界面。这样命令行测试（``tools/project_test.py``）不用启动
Tkinter 就能验证「存进去再读出来，值一模一样」，界面层只负责调两个函数。

文件长什么样
-----------
::

    {
      "format": "yang-switchkit-project",     ← 认领标记，不是本工具的文件直接拒
      "format_version": 1,                    ← 格式版本，将来改结构时升
      "app_version": "2.0.0",                 ← 哪个版本存的，排障用
      "saved_at": "2026-10-24T21:20:00+08:00",
      "vendor": "h3c",                        ← 当前选中的厂商
      "comments": true,                       ← 「保留注释」开关
      "theme": "light",
      "order": ["vlan_port", "lag"],          ← 已生成的场景，按生成顺序
      "params": {                             ← 四家的参数各存一份
        "h3c":    {"vlan_port": {"vlans": "10,20,30"}},
        "huawei": {...}
      }
    }

四家都存，是因为程序本来就按厂商分别保存参数，切厂商不丢值。
方案文件跟着这个行为走，打开后才能来回切厂商看同一套规划。

容错
----
读取时做三层清洗，任何一层出问题都只丢那一条，不整份拒绝：

* 未知厂商 / 未知场景 / 未知参数 → 丢弃，记一条警告交给界面提示
  （典型场景：用户拿新版本存的方案在旧版本里打开）
* 参数值统一转成字符串，``null`` 变成空串
* 已生成列表去掉不存在的场景、去重、保持原顺序

真正整份拒绝只有两种情况：不是 JSON、不是本工具的文件、
或者格式版本比当前程序新（结构可能不兼容，硬读会出错）。
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime

from . import __version__, registry
from .vendors import VENDORS

#: 认领标记，写在文件里用来判断「这是不是本工具的方案文件」
FORMAT = "yang-switchkit-project"

#: 格式版本。将来结构变化（比如参数分组方式改了）时加一，
#: 并在 ``load()`` 里为旧版本写迁移逻辑。
FORMAT_VERSION = 1

SUFFIX = ".json"
FILETYPES = [("Yang-SwitchKit 方案", "*.json"), ("所有文件", "*.*")]


class ProjectError(ValueError):
    """方案文件读不了。消息直接可以拿去做弹窗文案。"""


@dataclass
class Project:
    """一个方案的全部状态。字段跟界面状态一一对应。"""

    vendor: str = ""
    params: dict = field(default_factory=dict)
    order: list = field(default_factory=list)
    comments: bool = True
    theme: str = "light"

    def count(self) -> int:
        """填过值的场景数（任意厂商），用来在状态栏显示。"""
        return sum(len(v) for v in self.params.values())


def _known_vendors() -> tuple:
    return tuple(v.key for v in VENDORS)


def _known_scenarios() -> dict:
    return {s.key: tuple(p.key for p in s.params) for s in registry.SCENARIOS}


def _clean_params(raw, warnings: list) -> dict:
    """剔掉未知厂商 / 场景 / 参数，值统一成字符串。"""
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append("参数区格式不对，已忽略")
        return {}

    vendors = _known_vendors()
    scenes = _known_scenarios()
    out: dict = {}

    for vendor_key, vendor_params in raw.items():
        if vendor_key not in vendors:
            warnings.append(f"忽略了未知厂商「{vendor_key}」")
            continue
        if not isinstance(vendor_params, dict):
            continue

        kept: dict = {}
        for scenario_key, values in vendor_params.items():
            if scenario_key not in scenes:
                warnings.append(f"忽略了未知场景「{scenario_key}」，可能来自更新的版本")
                continue
            if not isinstance(values, dict):
                continue
            known_param_keys = scenes[scenario_key]
            dropped = [k for k in values if k not in known_param_keys]
            if dropped:
                warnings.append(
                    f"场景「{scenario_key}」里有 {len(dropped)} 个不认识的参数，已忽略")
            kept[scenario_key] = {
                k: ("" if v is None else str(v))
                for k, v in values.items() if k in known_param_keys
            }
        if kept:
            out[vendor_key] = kept

    return out


def _clean_order(raw, warnings: list) -> list:
    """去重、去未知场景、保持原顺序。"""
    if not isinstance(raw, (list, tuple)):
        return []
    known = _known_scenarios()
    seen: set = set()
    out: list = []
    for key in raw:
        if key in seen:
            continue
        if key not in known:
            warnings.append(f"忽略已生成列表里的未知场景「{key}」")
            continue
        seen.add(key)
        out.append(key)
    return out


def dump(project: Project) -> str:
    """序列化成 JSON 文本。写出去的内容一定是合法的（未知项会被丢掉）。"""
    warnings: list = []
    vendor = project.vendor if project.vendor in _known_vendors() else (
        VENDORS[0].key if VENDORS else "")
    payload = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "vendor": vendor,
        "comments": bool(project.comments),
        "theme": "dark" if project.theme == "dark" else "light",
        "order": _clean_order(project.order, warnings),
        "params": _clean_params(project.params, warnings),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def load(text: str) -> tuple[Project, list]:
    """解析 JSON 文本，返回 ``(方案, 警告列表)``。

    不是本工具的文件、或者格式版本太新，抛 :class:`ProjectError`；
    其余问题只丢对应条目并记进警告。
    """
    warnings: list = []

    if not text.strip():
        raise ProjectError("文件是空的。")

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectError(
            f"不是合法的 JSON 文件（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）。\n\n"
            "如果这是从别处复制来的配置文本，那它不是方案文件——"
            "方案文件由本工具的「保存方案」生成。") from None

    if not isinstance(raw, dict):
        raise ProjectError("文件内容不是一个方案对象，可能选错了文件。")

    if raw.get("format") != FORMAT:
        raise ProjectError(
            "这不是 Yang-SwitchKit 的方案文件。\n\n"
            "方案文件里有 \"format\": \"yang-switchkit-project\" 标记；"
            "如果你选的是导出的配置脚本（.txt），那个是给设备用的，不能当方案打开。")

    version = raw.get("format_version")
    if isinstance(version, int) and version > FORMAT_VERSION:
        raise ProjectError(
            f"这个方案由更新的版本保存（格式版本 {version}，本程序支持到 "
            f"{FORMAT_VERSION}）。\n\n请到项目主页下载新版本后再打开，"
            "勉强读取可能会漏掉部分参数。")

    vendor = raw.get("vendor")
    if vendor not in _known_vendors():
        if vendor:
            warnings.append(f"方案里的厂商「{vendor}」不认识，已切回默认厂商")
        vendor = VENDORS[0].key if VENDORS else ""

    project = Project(
        vendor=vendor,
        params=_clean_params(raw.get("params"), warnings),
        order=_clean_order(raw.get("order"), warnings),
        comments=bool(raw.get("comments", True)),
        theme="dark" if raw.get("theme") == "dark" else "light",
    )
    return project, warnings


def to_file(path, project: Project) -> pathlib.Path:
    """写文件。目录不存在时顺手建出来，省得用户先手动建。"""
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump(project), encoding="utf-8")
    return target


def from_file(path) -> tuple[Project, list]:
    """读文件。编码问题转成 :class:`ProjectError`，界面直接弹。"""
    target = pathlib.Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ProjectError(
            "文件不是 UTF-8 编码，读不出来。\n\n"
            "如果你用记事本改过这个文件，另存为时请选 UTF-8。") from None
    except OSError as exc:
        raise ProjectError(f"读不了这个文件：{exc}") from None
    return load(text)
