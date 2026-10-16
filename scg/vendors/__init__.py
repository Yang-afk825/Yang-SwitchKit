"""厂商命令库。

每个厂商模块暴露一个 ``VENDOR`` 元信息和一个 ``generate()`` 入口，
统一由本模块聚合，界面层只跟这里打交道。
"""
from __future__ import annotations

# 必须显式导入：PyInstaller 靠静态分析收集模块，动态 import 的模块不会被打进 exe
from . import cisco, h3c, huawei, ruijie
from ..model import GenResult, Vendor

_MODULES: dict[str, object] = {
    "h3c": h3c,
    "huawei": huawei,
    "cisco": cisco,
    "ruijie": ruijie,
}
_ORDER = ("h3c", "huawei", "cisco", "ruijie")

VENDORS: tuple[Vendor, ...] = tuple(_MODULES[k].VENDOR for k in _ORDER)
BY_KEY: dict[str, object] = dict(_MODULES)


def get_vendor(key: str) -> Vendor:
    mod = _MODULES.get(key)
    if mod is None:
        raise ValueError(f"未知厂商：{key}")
    return mod.VENDOR


def generate(vendor_key: str, scenario_key: str, params: dict, scenario) -> GenResult:
    """按厂商 + 场景生成命令。任何参数问题都以 ValueError 抛出，由界面提示。"""
    mod = _MODULES.get(vendor_key)
    if mod is None:
        raise ValueError(f"未知厂商：{vendor_key}")
    return mod.generate(scenario_key, params, scenario)


def supports(vendor_key: str, scenario_key: str) -> bool:
    mod = _MODULES.get(vendor_key)
    if mod is None:
        return False
    return scenario_key in getattr(mod, "_HANDLERS", {})


__all__ = ["VENDORS", "BY_KEY", "get_vendor", "generate", "supports"]
