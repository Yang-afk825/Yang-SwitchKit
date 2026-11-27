"""把公众号文章从 Markdown 转成 Word，图片内嵌。

    py -3.12 tools/md_to_docx.py [输出路径]

默认输出到桌面。排版按中文阅读习惯来：正文等线 11pt、行距 1.6、
段后 8pt，代码块沿用 Consolas 加浅灰底，图片按页宽等比缩放。

为什么不直接套 Word 内置样式
---------------------------
python-docx 的 Heading 默认是蓝色 Calibri，中文字体还得单独指定
（``rFonts`` 的 ``eastAsia`` 属性），不然中文会退到宋体而英文是 Calibri，
一篇里混两种字体，看着很业余。这里统一在样式上改一遍。
"""
from __future__ import annotations

import pathlib
import re
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "公众号-3.4.0.md"
DEFAULT_OUT = pathlib.Path.home() / "Desktop" / "配交换机时最烦的那几步.docx"

BODY_FONT = "等线"
HEAD_FONT = "微软雅黑"
CODE_FONT = "Consolas"
INK = RGBColor(0x1A, 0x1A, 0x1A)
GREY = RGBColor(0x66, 0x66, 0x66)

_IMG = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)\s*$")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def set_cn_font(run, name: str) -> None:
    """同时设西文与中文字体。

    只设 ``run.font.name`` 时，Word 只认西文那一半，中文会退回默认的宋体。
    """
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def shade(paragraph, color: str = "F6F6F6") -> None:
    ppr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    ppr.append(shd)


def rule(paragraph) -> None:
    """一段的分隔线：给段落加下边框，比插一条横线图片干净。"""
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "DDDDDD")
    borders.append(bottom)
    ppr.append(borders)


def add_rich(paragraph, text: str, size: int = 11) -> None:
    """写一段带加粗的文字。``**`` 之间的部分加粗。"""
    pos = 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            run = paragraph.add_run(text[pos:m.start()])
            set_cn_font(run, BODY_FONT)
            run.font.size = Pt(size)
            run.font.color.rgb = INK
        run = paragraph.add_run(m.group(1))
        set_cn_font(run, BODY_FONT)
        run.font.size = Pt(size)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        pos = m.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        set_cn_font(run, BODY_FONT)
        run.font.size = Pt(size)
        run.font.color.rgb = INK


def style_base(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.size = Pt(11)
    normal.font.color.rgb = INK
    set_cn_font_style(normal, BODY_FONT)
    normal.paragraph_format.line_spacing = 1.6
    normal.paragraph_format.space_after = Pt(8)

    for name, size in (("Heading 1", 18), ("Heading 2", 13)):
        style = doc.styles[name]
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0x11, 0x11, 0x11)
        set_cn_font_style(style, HEAD_FONT)
        style.paragraph_format.space_before = Pt(18 if name == "Heading 1" else 22)
        style.paragraph_format.space_after = Pt(10)
        style.paragraph_format.line_spacing = 1.35


def set_cn_font_style(style, name: str) -> None:
    """样式级别的中文字体。和 run 级别一样，必须显式写 eastAsia。"""
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def build(md_path: pathlib.Path, out_path: pathlib.Path) -> None:
    doc = Document()
    style_base(doc)

    # A4 + 窄一点的页边距，正文宽度约 16cm，截图放得下
    section = doc.sections[0]
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.4)
    section.right_margin = Cm(2.4)

    props = doc.core_properties
    props.title = "配交换机时最烦的那几步，我这次做进工具里了"
    props.author = "小阳的赛博日记"
    props.comments = "公众号：小阳的赛博日记"

    lines = md_path.read_text(encoding="utf-8").split("\n")
    in_code = False
    code_buf: list = []
    para_buf: list = []
    in_list = False

    def flush_para() -> None:
        if para_buf:
            p = doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(0)
            add_rich(p, " ".join(para_buf))
            para_buf.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_para()
            if in_code:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.4)
                p.paragraph_format.right_indent = Cm(0.2)
                p.paragraph_format.line_spacing = 1.35
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(12)
                shade(p)
                run = p.add_run("\n".join(code_buf))
                set_cn_font(run, CODE_FONT)
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x2B, 0x2B, 0x2B)
                code_buf.clear()
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_buf.append(raw)
            continue

        if not stripped:
            flush_para()
            continue

        if stripped == "---":
            flush_para()
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(14)
            rule(p)
            continue

        if stripped.startswith("## "):
            flush_para()
            doc.add_heading(stripped[3:], level=2)
            continue

        if stripped.startswith("# "):
            flush_para()
            h = doc.add_heading(stripped[2:], level=1)
            h.alignment = WD_ALIGN_PARAGRAPH.LEFT
            continue

        m = _IMG.match(stripped)
        if m:
            flush_para()
            target = md_path.parent / m.group(1)
            if target.is_file():
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(14)
                p.add_run().add_picture(str(target), width=Cm(15.2))
            else:
                doc.add_paragraph(f"［缺图：{m.group(1)}］")
            continue

        if stripped.startswith("- "):
            flush_para()
            style = "List Bullet"
            p = doc.add_paragraph(style=style)
            p.paragraph_format.space_after = Pt(4)
            add_rich(p, stripped[2:], size=10.5)
            in_list = True
            continue

        if stripped.startswith("小阳的赛博日记"):
            flush_para()
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(24)
            run = p.add_run(stripped)
            set_cn_font(run, BODY_FONT)
            run.font.size = Pt(10)
            run.font.color.rgb = GREY
            continue

        if in_list:
            in_list = False
        para_buf.append(stripped)

    flush_para()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    print(f"已保存 {out_path}")
    print(f"大小 {out_path.stat().st_size / 1024:.0f} KB")


def main() -> int:
    if not MD.is_file():
        print(f"找不到 {MD}")
        return 1
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    build(MD, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
