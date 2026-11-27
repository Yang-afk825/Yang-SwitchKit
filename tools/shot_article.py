"""生成公众号配图：把界面摆到指定状态，逐张截图。

和 tools/shot.py 的区别：那个是开发时随手看效果，这个要产出固定几张
「能放进文章」的图，所以每一步都摆好具体数据，跑完就能直接用。

    py -3.12 tools/shot_article.py

产物在 docs/images/article-340/。
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.wincap import grab_toplevel, grab_window    # noqa: E402

from scg.ui import ai_page as ai_ui           # noqa: E402
from scg.ui.window import App                 # noqa: E402

OUT = ROOT / "docs" / "images" / "article-340"

#: 现网配置样本。用 H3C 的写法，读出来的字段最全
SITE = """\
#
 sysname Access-SW-03
#
 vlan 10
  name Office
#
 vlan 20
  name Server
#
 vlan 30
  name Guest
#
interface Vlan-interface 1
 ip address 192.168.1.12 255.255.255.0
#
interface GigabitEthernet1/0/24
 port link-type trunk
 port trunk permit vlan 10 20 30
 port trunk pvid vlan 1
 description Uplink-to-Core
#
interface GigabitEthernet1/0/1
 port link-type access
 port access vlan 10
#
interface GigabitEthernet1/0/2
 port link-type access
 port access vlan 10
#
 ip route-static 0.0.0.0 0 192.168.1.1
#
return
"""

TRUNK_PARAMS = {
    "vlans": "10,20,30", "vlan_names": "", "port_type": "trunk",
    "ports": "1/0/24", "allowed": "10,20,30", "pvid": "1",
}


def settle(app) -> None:
    """窗口刚改完状态时，画面可能还停在上一步，多刷几帧再抓。"""
    for delay in (0.0, 0.12, 0.28):
        if delay:
            time.sleep(delay)
        app.update_idletasks()
        app.update()


def shot(widget, name: str, popup: bool = False) -> None:
    settle(widget.winfo_toplevel())
    img = grab_toplevel(widget) if popup else grab_window(widget)
    path = OUT / name
    img.save(path)
    print(f"  saved {path.name}  {img.size[0]}x{img.size[1]}", flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = App()
    app.geometry("1360x980+60+20")

    # ---------------------------------------------------------------
    print("一、AI 助手页：本端 + 对端")
    # ---------------------------------------------------------------
    app.vendor_var.set("h3c")
    app.vendor_key = "h3c"
    app._restore_fields()
    app.store[("h3c", "vlan_port")] = dict(TRUNK_PARAMS)
    app._restore_fields()
    app._generate("vlan_port")

    page = app.ai_page
    page._show_commands("vlan_port", "h3c")
    peer = dict(TRUNK_PARAMS)
    peer["ports"] = "1/0/1"
    page.apply_peer(peer, "1/0/1")
    app._set_status()
    app.pages.select(app.page_ai)
    app.update_idletasks()
    app.update()
    # 结果区压到最小，命令区留大，再把滚动条停在两段的接缝上，
    # 这样一张图能同时看到本端尾巴和对端开头
    try:
        page.result_paned.sashpos(0, 105)
        page.cmd_out.yview("11.0")   # 本端尾巴 + 分隔行 + 对端开头同屏
    except Exception:
        pass
    shot(app, "01-ai-peer.png")

    # ---------------------------------------------------------------
    print("二、从现网导入")
    # ---------------------------------------------------------------
    dialog = ai_ui.ImportDialog(app, page)
    dialog.input.insert("1.0", SITE)
    dialog._parse()
    shot(dialog, "02-import.png", popup=True)
    dialog.grab_release()
    dialog.destroy()

    # ---------------------------------------------------------------
    print("三、配置体检（安全体检 + 语法校对）")
    # ---------------------------------------------------------------
    app.pages.select(app.page_audit)
    app.audit_in.insert("1.0", SITE + "\n port trunk permitt vlan 40\n")
    app._run_audit()
    shot(app, "03-audit.png")

    # ---------------------------------------------------------------
    print("四、语法与结构校对")
    # ---------------------------------------------------------------
    from scg import audit as audit_mod
    from scg.ai import rules as rules_mod

    bad = ("sysname SW-Test\n"
           "vlan 10\n"
           "port link-type access\n"
           "interface GigabitEthernet1/0/1\n"
           " port access vlan 10\n"
           "interface GigabitEthernet1/0/2\n"
           " port trunk permitt vlan 20\n")
    app.pages.select(app.page_audit)
    app.audit_in.delete("1.0", "end")
    app.audit_in.insert("1.0", bad)
    app.audit_source = bad
    # 把安全体检那份报告换成空的，语法段就排到首屏，不用滚动
    app._last_audit = audit_mod.Report(vendor_name="H3C",
                                       vendor_evidence="示例：只看语法段")
    app._syntax = rules_mod.check("h3c", bad)
    app._refresh_audit()
    app._set_status()
    shot(app, "05-syntax.png")

    # ---------------------------------------------------------------
    print("五、回到主页面")
    # ---------------------------------------------------------------
    app.pages.select(app.page_main)

    print("\n完成，产物在", OUT)
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
