"""把公众号文章从 Markdown 转成「浏览器里打开、全选复制、粘进公众号」的网页。

为什么是 HTML
------------
微信公众号编辑器不认 Markdown：反引号会原样显示，`![]()` 不会变成图，
``` 也不会变成代码块。但它认**富文本**——从网页复制过去，标题、加粗、
列表、代码块、图片都能带过去。

所以最省事的流程是：浏览器打开这个 html → Ctrl+A → Ctrl+C → 粘到编辑器。
图片用 base64 内嵌，粘过去的时候微信会自动收进自己的图床（会问一下）。

    py -3.12 tools/md_to_wechat.py

产物：docs/公众号-3.4.0.html
"""
from __future__ import annotations

import base64
import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "公众号-3.4.0.md"
OUT = ROOT / "docs" / "公众号-3.4.0.html"

#: 正文样式。公众号正文一般是 16px / 1.8 行高，代码块浅灰底
CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 80px;
  background: #fff; color: #2b2b2b;
  font: 16px/1.8 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
#article { max-width: 700px; margin: 0 auto; }
h1 {
  font-size: 23px; line-height: 1.45; font-weight: 700;
  margin: 0 0 28px; color: #111; letter-spacing: .2px;
}
h2 {
  font-size: 18px; font-weight: 700; color: #111;
  margin: 40px 0 16px; line-height: 1.5;
}
p { margin: 0 0 18px; text-align: justify; }
strong { font-weight: 700; color: #111; }
hr { border: none; border-top: 1px solid #ececec; margin: 32px 0; }
ul { margin: 0 0 18px; padding-left: 22px; }
li { margin-bottom: 7px; line-height: 1.8; }
pre {
  margin: 0 0 20px; padding: 15px 17px;
  background: #f6f6f6; border-radius: 4px;
  font: 13px/1.7 Consolas, "Courier New", monospace;
  color: #2b2b2b; white-space: pre-wrap; word-break: break-all;
}
img { display: block; max-width: 100%; height: auto; margin: 22px auto; }
.sign { margin-top: 40px; color: #888; font-size: 14px; }
"""

_IMG = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)\s*$")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def to_data_uri(path: pathlib.Path) -> str:
    """图片转 base64 内嵌。

    不内嵌的话，复制到编辑器时图片指向本地 file:// 路径，微信传不上去，
    粘完只剩一个破图。base64 是让「复制」这个动作能带上图片的唯一办法。
    """
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    return f"data:image/{mime};base64,{data}"


def inline(text: str) -> str:
    """行内处理：转义 + 加粗。"""
    out = html.escape(text)
    return _BOLD.sub(r"<strong>\1</strong>", out)


def convert(md_text: str, base: pathlib.Path) -> str:
    lines = md_text.split("\n")
    out: list = []
    in_code = False
    buf: list = []          # 正在攒的代码块
    in_list = False
    para: list = []         # 正在攒的段落

    def flush_para() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()

        # 代码块
        if stripped.startswith("```"):
            flush_para()
            close_list()
            if in_code:
                out.append("<pre>" + html.escape("\n".join(buf)) + "</pre>")
                buf.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            buf.append(line.rstrip())
            continue

        # 空行
        if not stripped:
            flush_para()
            close_list()
            continue

        # 分隔线
        if stripped == "---":
            flush_para()
            close_list()
            out.append("<hr>")
            continue

        # 标题
        if stripped.startswith("## "):
            flush_para()
            close_list()
            out.append(f"<h2>{inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            flush_para()
            close_list()
            out.append(f"<h1>{inline(stripped[2:])}</h1>")
            continue

        # 图片
        m = _IMG.match(stripped)
        if m:
            flush_para()
            close_list()
            target = base / m.group(1)
            if target.is_file():
                out.append(f'<img src="{to_data_uri(target)}" alt="">')
            else:
                out.append(f'<p style="color:#c00">［缺图：{html.escape(m.group(1))}］</p>')
            continue

        # 列表
        if stripped.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(stripped[2:])}</li>")
            continue

        # 署名行
        if stripped.startswith("小阳的赛博日记"):
            flush_para()
            close_list()
            out.append(f'<p class="sign">{inline(stripped)}</p>')
            continue

        # 普通段落
        close_list()
        para.append(stripped)

    flush_para()
    close_list()
    if in_code and buf:
        out.append("<pre>" + html.escape("\n".join(buf)) + "</pre>")
    return "\n".join(out)


def main() -> int:
    if not MD.is_file():
        print(f"找不到 {MD}")
        return 1

    body = convert(MD.read_text(encoding="utf-8"), MD.parent)
    page = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>配交换机时最烦的那几步</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        f"<div id=\"article\">\n{body}\n</div>\n</body>\n</html>\n"
    )
    OUT.write_text(page, encoding="utf-8")
    print(f"已生成 {OUT}")
    print(f"大小 {OUT.stat().st_size / 1024:.0f} KB（图片已内嵌）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
