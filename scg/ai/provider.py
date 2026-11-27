"""传输层：唯一会发网络请求的地方。

只实现一种协议
-------------
OpenAI 的 ``/chat/completions`` 事实上已经成了行业接口。DeepSeek、通义、
智谱、月之暗面、vLLM、ollama 全都提供兼容实现，区别只在 ``base_url``
和 ``model`` 两个字符串。写一个类 + 可配置地址，覆盖全部，
换供应商不用改代码、不用发版。

为什么用 urllib 而不是 requests
------------------------------
这个项目打包成 11 MB 单文件，卖点是双击即用、不装环境。多引入一个
第三方库就会多一层 PyInstaller 的隐式依赖风险，而我们要的只是
「POST 一段 json，逐行读回响应」这一件事，标准库够用。

本模块不做的事
-------------
不碰 Tkinter，不碰 registry，不拼业务提示词。它只认 messages 进、
文本出。这样 ``tools/ai_test.py`` 可以在没有界面、没有网络的情况下
用假响应测完整个解析链路。
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request

from .config import OVERALL_DEADLINE, AIConfig

#: 有些网关会拦空 UA，带一个正常的
_USER_AGENT = "YangSwitchKit/2.2 (+https://github.com/Yang-afk825)"

#: 错误码 → 给用户看的一句话。顺序无关，按 code 取。
_MESSAGES = {
    "not_configured": "还没配置接口地址和密钥，当前是纯离线模式",
    "network": "连不上服务器，检查网络或接口地址",
    "timeout": "服务器没有在限定时间内响应，可以重试",
    "auth": "密钥无效或已失效，去设置里重新填",
    "rate_limit": "请求太频繁或余额不足，稍后再试",
    "bad_request": "服务端拒绝了请求，多半是模型名写错了",
    "server": "服务端出错，不是你的问题，稍后重试",
    "bad_format": "模型没有按要求返回结构化的内容",
    "web_unavailable": "当前版本没有可用的联网组件",
}


class AIError(Exception):
    """一次 AI 调用失败。``code`` 给程序判断，``message`` 直接给用户看。"""

    def __init__(self, code: str, message: str = "", raw: str = ""):
        self.code = code
        self.raw = raw
        super().__init__(message or _MESSAGES.get(code, "调用失败"))

    def user_message(self) -> str:
        return str(self) or _MESSAGES.get(self.code, "调用失败")


def extract_json(text: str):
    """从模型回复里抠出 JSON。

    模型很少老老实实只吐一段 json：常见的是套一层 ```json 围栏、
    前面加一句「好的，解析结果如下：」、或者在末尾再补一句解释。
    三种都兜住，兜不住抛 bad_format 并把原文带上，让用户自己判断。
    """
    body = (text or "").strip()
    if not body:
        raise AIError("bad_format", "模型返回了空内容", text)

    fenced = re.search(r"```(?:json|JSON)?\s*(.+?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()

    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        pass

    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(body[start:end + 1])
        except Exception:  # noqa: BLE001
            pass

    raise AIError("bad_format", "模型返回的不是合法 JSON，原文已保留", text)


class Provider:
    """一次请求一个实例，不要跨线程复用。

    ``on_chunk`` 会在**调用方所在线程**里被反复调用（也就是 worker 线程）。
    它只应该做「把文本塞进队列」这种动作，绝不能碰任何界面控件。
    """

    def __init__(self, cfg: AIConfig, on_chunk=None):
        self.cfg = cfg
        self.on_chunk = on_chunk
        self.last_raw = ""      # 上一次的完整响应原文，排查用

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def chat(self, messages: list, want_json: bool = True) -> str:
        """发一轮对话，返回模型输出的完整文本。"""
        if not self.cfg.ready():
            raise AIError("not_configured")

        try:
            return self._post(messages, want_json)
        except AIError as exc:
            # 部分服务端不认 response_format，400 且提到这个字段时去掉重试一次。
            # 这不是「自动重试」——自动重试是指超时/限流后重发，那是替用户烧钱；
            # 这里是一次协议兼容性降级，只发生一次，且只在明确是这个原因时。
            if (want_json and exc.code == "bad_request"
                    and "response_format" in (exc.raw or "").lower()):
                return self._post(messages, False)
            raise

    def test(self) -> str:
        """连通性 + 鉴权测试：只要 1 个 token，几乎不花钱。"""
        messages = [{"role": "user", "content": "ping"}]
        text = self._post(messages, False, max_tokens=1)
        return text or "ok"

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _payload(self, messages: list, want_json: bool, max_tokens: int) -> dict:
        body = {
            "model": self.cfg.model,
            "messages": messages,
            # 这是参数抽取任务，不是创作。温度高只会让它在端口号上瞎编。
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": bool(self.cfg.stream),
        }
        if want_json:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, messages: list, want_json: bool, max_tokens: int = 1200) -> str:
        payload = json.dumps(self._payload(messages, want_json, max_tokens),
                             ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.cfg.endpoint(),
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.key}",
                "User-Agent": _USER_AGENT,
                # 明确要求不压缩：省掉一层 gzip 解包，也避免个别网关强制压缩
                "Accept-Encoding": "identity",
                "Accept": "text/event-stream" if self.cfg.stream else "application/json",
            },
        )

        deadline = time.monotonic() + OVERALL_DEADLINE
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                if self.cfg.stream:
                    text = self._read_stream(resp, deadline)
                else:
                    text = self._read_plain(resp)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise AIError("timeout") from None
            raise AIError("network", f"连不上服务器：{reason}") from None
        except (socket.timeout, TimeoutError):
            raise AIError("timeout") from None
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 读流过程中断、解码失败等
            raise AIError("network", f"请求中断：{exc}") from None

        self.last_raw = text
        return text

    def _read_plain(self, resp) -> str:
        raw = resp.read()
        body = raw.decode("utf-8", "replace")
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            raise AIError("bad_format", "服务端返回的不是 JSON", body[:800]) from None
        return self._content_of(data, body)

    def _read_stream(self, resp, deadline: float) -> str:
        """逐行读 SSE。

        只做展示用的增量回调，最终结果仍然是拼起来的完整文本——
        不做增量 JSON 解析。模型的 json 可能在第 200 个字才闭合，
        边收边解析只会带来一堆半截状态。

        有些网关会忽略 ``stream`` 参数、直接返回一整段普通 JSON。
        所以非 ``data:`` 开头的行不直接丢，收起来做兜底解析。
        """
        parts: list = []
        plain: list = []

        for raw in resp:
            if time.monotonic() > deadline:
                raise AIError("timeout", f"响应超过 {OVERALL_DEADLINE} 秒，已中断")
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                # 网关忽略了 stream，或者在 SSE 之前先吐了一行纯 json 错误体
                if line.startswith("{"):
                    plain.append(line)
                continue

            payload = line[5:].strip()      # 注意 data: 后面可能有也可能没有空格
            if payload == "[DONE]":
                break
            if not payload:
                continue

            try:
                chunk = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue                    # 单条坏片段不该让整次请求失败

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                parts.append(piece)
                if self.on_chunk is not None:
                    self.on_chunk(piece)
            if choices[0].get("finish_reason"):
                # 结束标记之后可能还有一行 [DONE]，但内容已经完整了
                break

        text = "".join(parts)
        if text.strip():
            return text

        # 兜底：压根没有 SSE 帧，按普通 JSON 处理
        if plain:
            body = "".join(plain)
            try:
                return self._content_of(json.loads(body), body)
            except AIError:
                raise
            except Exception:  # noqa: BLE001
                raise AIError("bad_format", "服务端返回的内容无法解析", body[:800]) from None

        raise AIError("bad_format", "服务端返回了空响应", text)

    def _content_of(self, data: dict, raw: str) -> str:
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception:  # noqa: BLE001
            err = (data.get("error") or {}).get("message") if isinstance(data, dict) else ""
            raise AIError("bad_format", err or "响应里没有可用的内容", raw[:800]) from None

    def _http_error(self, exc: urllib.error.HTTPError) -> AIError:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""

        detail = ""
        try:
            parsed = json.loads(body)
            detail = ((parsed.get("error") or {}).get("message")
                      or parsed.get("message") or "")
        except Exception:  # noqa: BLE001
            detail = body[:200].strip()

        code = {401: "auth", 403: "auth", 429: "rate_limit"}.get(exc.code, "")
        if not code:
            code = "bad_request" if 400 <= exc.code < 500 else "server"

        base = _MESSAGES.get(code, "调用失败")
        msg = f"{base}（HTTP {exc.code}）" + (f"：{detail}" if detail else "")
        return AIError(code, msg, raw=body[:800])


def probe(cfg: AIConfig) -> tuple:
    """设置页的「测试连接」。返回 (成功与否, 一句话)。不抛异常。"""
    try:
        Provider(cfg).test()
    except AIError as exc:
        return False, exc.user_message()
    except Exception as exc:  # noqa: BLE001
        return False, f"测试失败：{exc}"
    return True, f"连通正常，模型 {cfg.model}"
