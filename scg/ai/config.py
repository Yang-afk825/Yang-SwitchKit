"""AI 模式的配置：接口地址、模型名、密钥、开关。

密钥怎么存
---------
Windows 自带的 ``crypt32.dll`` 提供 ``CryptProtectData``，用 ``ctypes`` 调即可，
不需要任何第三方加密库。加密后的密文只有「本机 + 当前 Windows 用户」能解开。

换机器或换 Windows 用户后解不开——这是**预期行为**，不是故障。
解不开时把密钥当没配置处理，提示重填，不抛异常把启动流程搞崩。

为什么不写进方案文件
-------------------
方案 ``.json`` 是要进 git、要发给同事、要作为交付物交接的（见 README）。
密钥一旦混进去就是一次凭据泄露。``project.Project`` 的字段里不允许出现
任何 AI 配置，这条是硬的。
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
from dataclasses import dataclass, field

#: 配置文件名，放在 %APPDATA%\YangSwitchKit\ 下
APP_DIR = "YangSwitchKit"
FILE_NAME = "ai.json"

#: 认领标记，跟方案文件一样的做法，防止误读别人的 json
FORMAT = "yang-switchkit-ai"
FORMAT_VERSION = 1

#: 默认接口地址与模型。选 DeepSeek 是因为它价格低、中文够用，
#: 而且完全兼容 OpenAI 协议，用户想换别家只改地址不改代码。
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 15

#: 单次对话的整体上限（秒）。发起连接有 timeout 管，
#: 但流式响应一旦开始，chunk 之间的停顿时长不受 timeout 约束，
#: 得靠这个看门狗兜底。
OVERALL_DEADLINE = 45

#: DPAPI 标志：禁止弹任何系统对话框（我们在后台线程里调用，弹窗会卡住）
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class ConfigError(ValueError):
    """配置读写失败。消息直接给用户看，不要塞 traceback。"""


# ==========================================================================
# DPAPI：只依赖 ctypes + crypt32.dll
# ==========================================================================

try:  # 非 Windows 上导入失败是正常的，测试脚本不依赖加密
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    HAS_DPAPI = True
except Exception:  # noqa: BLE001 —— 缺 DLL、非 Windows、权限受限都归到这一类
    HAS_DPAPI = False


def _to_blob(data: bytes):
    """把 bytes 包成 DATA_BLOB。

    必须同时把底层 buffer 返回给调用方持有：只返回 blob 的话，
    buffer 会在函数返回后被回收，``pbData`` 变成悬空指针，
    CryptProtectData 读到的是垃圾。
    """
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    return blob, buf


def _free(blob) -> None:
    """释放 DPAPI 分配的内存。指针类型先转成 c_void_p，避免 64 位下的转换歧义。"""
    if blob.pbData:
        _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def protect(plain: str) -> str:
    """明文 → 可存进 json 的 base64 密文。"""
    if not HAS_DPAPI:
        raise ConfigError("当前系统没有 DPAPI，无法安全保存密钥")

    raw = plain.encode("utf-8")
    if not raw:
        return ""
    in_blob, _keep = _to_blob(raw)
    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob), "YangSwitchKit", None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise ConfigError(f"加密密钥失败（错误码 {ctypes.get_last_error()}）")
    try:
        return base64.b64encode(
            ctypes.string_at(out_blob.pbData, out_blob.cbData)).decode("ascii")
    finally:
        _free(out_blob)


def unprotect(cipher: str) -> str:
    """base64 密文 → 明文。解不开返回空串，由调用方当「没配置」处理。"""
    if not HAS_DPAPI or not cipher:
        return ""
    try:
        raw = base64.b64decode(cipher)
    except Exception:  # noqa: BLE001
        return ""
    if not raw:
        return ""

    in_blob, _keep = _to_blob(raw)
    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        # 换机器 / 换 Windows 用户 / 配置文件被人拷来 —— 都会走到这里
        return ""
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8", "replace")
    finally:
        _free(out_blob)


# ==========================================================================
# 配置对象
# ==========================================================================

@dataclass
class AIConfig:
    """一份 AI 设置。``key`` 只在内存里是明文。

    没有独立的「启用」开关
    ----------------------
    早期版本有个 ``enabled`` 字段，默认 False，要在设置里单独勾一下才算启用。
    结果就是：用户填了密钥、点保存、测试连接也能通，主界面却仍然显示
    「纯离线」——因为测试连接只看密钥，可用性判断却还要求那个没勾的开关。
    一个状态两个真相，必然对不上。

    现在只有一件事决定能不能用：**密钥填了就是能用，清掉就是停用**。
    少一个状态，就少一类这种错。
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    # 明文密钥。repr=False 是为了不让它出现在任何调试输出 / 异常信息里
    key: str = field(default="", repr=False)
    timeout: int = DEFAULT_TIMEOUT
    redact: bool = True
    stream: bool = True
    # 上次保存时密钥解不开（换了机器或换了用户），界面据此提示重填
    key_broken: bool = False

    def ready(self) -> bool:
        """能不能发起在线请求。有密钥、有地址、有模型名就算。"""
        return bool(self.key and self.base_url and self.model)

    def endpoint(self, path: str = "/chat/completions") -> str:
        base = (self.base_url or "").rstrip("/")
        return base + path

    def masked_key(self) -> str:
        """给界面显示用，永远不显示完整密钥。"""
        if not self.key:
            return ""
        if len(self.key) <= 10:
            return "•" * len(self.key)
        return f"{self.key[:4]}{'•' * 8}{self.key[-4:]}"


def config_dir() -> pathlib.Path:
    """%APPDATA%\\YangSwitchKit。拿不到 APPDATA 时退回用户主目录。"""
    root = os.environ.get("APPDATA")
    base = pathlib.Path(root) if root else pathlib.Path.home() / ".config"
    return base / APP_DIR


def config_path() -> pathlib.Path:
    return config_dir() / FILE_NAME


def load(path=None) -> AIConfig:
    """读配置。文件不存在或内容坏了都返回默认值，绝不抛异常。

    这个函数在启动链路里被调用，它一崩整个程序就起不来——
    为了一个可选功能把主程序拖死是不划算的。

    ``path`` 只给测试用：``tools/ai_test.py`` 要验证「存进去再读出来，
    ready() 结论一致」，不能拿用户真实的配置文件做实验。
    """
    cfg = AIConfig()
    target = pathlib.Path(path) if path else config_path()
    if not target.is_file():
        return cfg

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 坏 json / 无权限 / 编码错，一律当没配
        return cfg

    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        return cfg

    cfg.base_url = str(raw.get("base_url") or DEFAULT_BASE_URL).strip()
    cfg.model = str(raw.get("model") or DEFAULT_MODEL).strip()
    cfg.timeout = _clamp_int(raw.get("timeout"), DEFAULT_TIMEOUT, 3, 120)
    cfg.redact = bool(raw.get("redact", True))
    cfg.stream = bool(raw.get("stream", True))

    cipher = raw.get("key_protected") or ""
    if cipher:
        cfg.key = unprotect(str(cipher))
        cfg.key_broken = not cfg.key
    return cfg


def save(cfg: AIConfig, path=None) -> pathlib.Path:
    """写配置。密钥先过 DPAPI 再落盘。``path`` 同样只给测试用。"""
    target = pathlib.Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "base_url": cfg.base_url.strip(),
        "model": cfg.model.strip(),
        "key_protected": protect(cfg.key) if cfg.key else "",
        "timeout": cfg.timeout,
        "redact": cfg.redact,
        "stream": cfg.stream,
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)   # 原子替换，写到一半断电不会留下半个文件
    return target


def clear_key(cfg: AIConfig) -> None:
    """清掉密钥并把配置落盘。界面上的「清除密钥」走这里。"""
    cfg.key = ""
    cfg.key_broken = False
    save(cfg)


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))
