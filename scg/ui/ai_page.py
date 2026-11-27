"""AI 助手页：一句话把参数填进表单。

这个页面只做一件事
----------------
「给 VLAN 10 20 30 划 8 个 access 口」→ 识别场景、抽出参数、回填主页面表单、
生成命令。别的都不做。

命令查错不在这一页，在「配置体检」页——那里本来就有粘贴区、安全体检报告
和语法校对，再单独开一个查错入口只会让人不知道往哪粘。AI 的语义补充挂在
那一页的报告末尾。

延迟导入
-------
这个文件顶层不 import ``scg.ai``。真要用的时候才导（``_ai()``）。
没配 key 的用户启动时不会多加载任何东西，``scg/ai/`` 整个删掉主程序
也照样跑。

线程模型
-------
Tkinter 是单线程的。网络请求直接在主线程发，窗口会假死 2-15 秒，
Windows 上还会被标成「未响应」。固定套路：worker 线程只做计算和 IO，
结果放进 ``queue.Queue``；主线程用 ``after(100)`` 轮询。
worker 里绝不碰控件，也不用 ``self.after`` 从子线程回调。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import registry, snapshot
from ..vendors import VENDORS, get_vendor, supports
from . import theme as T

#: 主线程轮询间隔。100ms 对手感来说够了，也不会白烧 CPU
POLL_MS = 100

#: 这些参数是「口」。复制到对端时只换它们，其余一律照搬
_PORT_PARAM_KEYS = ("ports", "edge_ports", "trust_ports", "user_ports",
                    "uplink", "source", "monitor", "apply_port")


def _port_params(scenario, params: dict) -> list:
    """场景里属于「端口」的参数。返回 ``[(key, 标签, 当前值)]``。"""
    out: list = []
    for param in scenario.params:
        if param.key not in _PORT_PARAM_KEYS:
            continue
        value = str(params.get(param.key, "") or "").strip()
        out.append((param.key, param.label, value))
    return out


def _primary_port_param(scenario, params: dict):
    """挑一个作为「对端要换的那个口」。

    优先 ``ports``——绝大多数互联场景（VLAN、聚合、限速、隔离）用它。
    没有就退到第一个端口类参数（镜像是 source、ACL 是 apply_port）。
    """
    items = _port_params(scenario, params)
    if not items:
        return None
    for item in items:
        if item[0] == "ports":
            return item
    return items[0]


class AIPage:
    """挂在主窗口上的 AI 助手页。"""

    def __init__(self, app, parent: tk.Frame):
        self.app = app
        self.parent = parent
        self._conf = None          # 延迟加载的 AIConfig
        self._mods = None          # 延迟加载的 AI 子模块
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._chunk_count = 0

        # 命令区的状态：本端文本、对端文本、以及「复制到对端」要用的上下文
        self._local_text = ""
        self._peer_text = ""
        self._peer_label = ""
        self._pair_ctx = None      # (scenario_key, params, vendor_key)

        self._build()
        # 启动路径上不导入 scg/ai：等界面画完再去看配置状态。
        # 这条让「没配 key 的用户启动耗时不变」变成可验证的事实。
        self.app.after(200, self._refresh_mode)

    # ==================================================================
    # 懒加载
    # ==================================================================

    def _ai(self):
        """按需导入 AI 包。返回模块名字典，调用方按名字取。"""
        if self._mods is not None:
            return self._mods

        from ..ai import config, intent, provider, redact, rules, schema
        self._mods = {
            "config": config,
            "intent": intent,
            "provider": provider,
            "redact": redact,
            "rules": rules,
            "schema": schema,
        }
        return self._mods

    def _cfg(self):
        if self._conf is None:
            self._conf = self._ai()["config"].load()
        return self._conf

    def reload_config(self) -> None:
        """设置弹窗保存后调一下，把内存里的配置刷新掉。"""
        self._conf = None
        self._refresh_mode()

    # ==================================================================
    # 骨架
    # ==================================================================

    def _build(self) -> None:
        page = self.parent
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=1)

        # ---- 顶栏 ----------------------------------------------------
        bar = tk.Frame(page, background=T.BG)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, T.px(8)))
        bar.columnconfigure(3, weight=1)

        ttk.Label(bar, text="说一句要做什么，我帮你把参数填进表单",
                  style="Title.TLabel").grid(row=0, column=0, sticky="w")

        # 生成厂商：直接在这里选，解析时按这家的语法抽参数
        # （端口编号习惯不一样，选错厂商端口就会写错）
        ttk.Label(bar, text="生成厂商").grid(row=0, column=1, sticky="w",
                                          padx=(T.px(16), T.px(6)))
        self.vendor_box = ttk.Combobox(bar, state="readonly", width=9,
                                       font=T.FONT_UI)
        self.vendor_box["values"] = [v.name for v in VENDORS]
        self.vendor_box.grid(row=0, column=2, sticky="w")
        self.sync_vendor()

        self.mode_label = ttk.Label(bar, text="", style="Faint.TLabel")
        self.mode_label.grid(row=0, column=3, sticky="w", padx=(T.px(14), 0))

        ttk.Button(bar, text="设置", style="Ghost.TButton",
                   command=self._open_settings).grid(row=0, column=4, sticky="e")

        # ---- 输入区 --------------------------------------------------
        self.intent_input = tk.Text(page, height=4, background=T.FIELD,
                                    foreground=T.FG, insertbackground=T.FG,
                                    font=T.FONT_UI, relief="flat", bd=0,
                                    wrap="word", padx=T.px(12), pady=T.px(10),
                                    selectbackground=T.SEL_BG,
                                    selectforeground=T.FG)
        self.intent_input.grid(row=1, column=0, sticky="ew", pady=(T.px(4), T.px(8)))
        self.intent_input.insert(
            "1.0", "给 VLAN 10 20 30 划 8 个 access 口，接办公电脑")
        self.intent_input.bind("<Control-Return>",
                               lambda e: self._run_intent() or "break")

        # ---- 按钮行 --------------------------------------------------
        tools = tk.Frame(page, background=T.BG)
        tools.grid(row=2, column=0, sticky="ew", pady=(0, T.px(8)))

        self.intent_btn = ttk.Button(tools, text="解析并填入表单",
                                     style="Gen.TButton", command=self._run_intent)
        self.intent_btn.pack(side="left")

        # 原来这里有个「参数齐了也先让我确认」勾选框。它的存在本身就是
        # 设计失误：勾上之后每句话都要多点一次，不勾又不知道自己该不该勾。
        # 参数真缺了会自己停下来问（见 _after_intent），不需要用户预先选。
        self.intent_status = ttk.Label(tools, text="Ctrl+Enter 也可以",
                                       style="Faint.TLabel")
        self.intent_status.pack(side="left", padx=(T.px(12), 0))

        # 现网导入：把设备上那段配置读成参数，不用手抄现有的 VLAN 和上联口
        ttk.Button(tools, text="从现网导入", style="Ghost.TButton",
                   command=self._open_import).pack(side="left",
                                                   padx=(T.px(12), 0))

        ttk.Button(tools, text="清空结果", style="Ghost.TButton",
                   command=self._clear).pack(side="right")

        # ---- 结果区：上解析、下命令 ----------------------------------
        # 原来是单块解析结果，生成完得切到主页面才看得见命令。多这一下没有意义：
        # 用户问「给 VLAN 划 8 个口」，要的就是命令本身，参数只是过程
        paned = ttk.PanedWindow(page, orient="vertical")
        paned.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.result_paned = paned

        self._build_parse_pane(paned)
        self._build_command_pane(paned)
        self.app.after(150, self._set_result_sash)

    def _set_result_sash(self) -> None:
        """上面解析结果给 45%，剩下给命令。命令是主角，但参数得够看。"""
        try:
            total = self.result_paned.winfo_height()
            if total > 120:
                self.result_paned.sashpos(0, int(total * 0.45))
        except tk.TclError:
            pass

    def _build_parse_pane(self, parent) -> None:
        pane = tk.Frame(parent, background=T.PANEL)
        parent.add(pane, weight=1)
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(0, weight=1)

        self.intent_out = tk.Text(pane, background=T.PANEL, foreground=T.FG,
                                  font=T.FONT_UI, relief="flat", bd=0,
                                  wrap="word", padx=T.px(16), pady=T.px(12),
                                  selectbackground=T.SEL_BG,
                                  selectforeground=T.FG)
        self.intent_out.grid(row=0, column=0, sticky="nsew")
        bar2 = ttk.Scrollbar(pane, orient="vertical", command=self.intent_out.yview)
        bar2.grid(row=0, column=1, sticky="ns")
        self.intent_out.configure(yscrollcommand=bar2.set)

        w = self.intent_out
        w.tag_configure("h1", foreground=T.C_HEAD,
                        font=("Microsoft YaHei UI", 11, "bold"),
                        spacing1=T.px(8), spacing3=T.px(5))
        w.tag_configure("body", foreground=T.FG)
        w.tag_configure("dim", foreground=T.FG_FAINT)
        w.tag_configure("ok", foreground=T.OK)
        w.tag_configure("warn", foreground=T.C_WARN)
        w.tag_configure("err", foreground=T.C_ERR)
        w.tag_configure("code", foreground=T.C_CMD, font=T.FONT_MONO)

        self._write_intent("可以这么说：", "dim")
        for example in (
            "给 VLAN 10 20 30 划 8 个 access 口，接办公电脑",
            "两台交换机做堆叠，成员号 1，堆叠口 49 和 50",
            "配 OSPF 宣告 192.168.1.0/24，router id 1.1.1.1",
            "1 到 8 口做端口隔离，同一个 VLAN 内互相不通",
        ):
            self._write_intent("  " + example, "body")

    def _build_command_pane(self, parent) -> None:
        pane = tk.Frame(parent, background=T.BG)
        parent.add(pane, weight=1)
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(1, weight=1)

        head = tk.Frame(pane, background=T.BG)
        head.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=T.px(2), pady=(T.px(6), T.px(4)))
        head.columnconfigure(1, weight=1)

        ttk.Label(head, text="生成的命令", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w")
        self.cmd_meta = ttk.Label(head, text="", style="Faint.TLabel")
        self.cmd_meta.grid(row=0, column=1, sticky="e", padx=(0, T.px(8)))

        # 两端互联时（trunk、聚合、VRRP 这类），本端配完要按同一份参数给对端
        # 也出一份，只换端口名。不这么做就得把参数重填一遍，也就是漏改的起点
        self.peer_btn = ttk.Button(head, text="复制到对端…",
                                   style="Ghost.TButton",
                                   command=self._open_peer, state="disabled")
        self.peer_btn.grid(row=0, column=2, sticky="e", padx=(0, T.px(8)))

        self.copy_local_btn = ttk.Button(head, text="复制本端",
                                         style="Ghost.TButton",
                                         command=self._copy_local)
        self.copy_local_btn.grid(row=0, column=3, sticky="e")
        self.copy_peer_btn = ttk.Button(head, text="复制对端",
                                        style="Ghost.TButton",
                                        command=self._copy_peer, state="disabled")
        self.copy_peer_btn.grid(row=0, column=4, sticky="e", padx=(T.px(6), 0))

        ttk.Button(head, text="去主页面", style="Ghost.TButton",
                   command=self._goto_main).grid(row=0, column=5, sticky="e",
                                                 padx=(T.px(6), 0))

        self.cmd_out = tk.Text(pane, background=T.FIELD, foreground=T.C_CMD,
                               font=T.FONT_MONO, relief="flat", bd=0,
                               wrap="none", padx=T.px(14), pady=T.px(10),
                               selectbackground=T.SEL_BG, selectforeground=T.FG)
        self.cmd_out.grid(row=1, column=0, sticky="nsew")
        bar = ttk.Scrollbar(pane, orient="vertical", command=self.cmd_out.yview)
        bar.grid(row=1, column=1, sticky="ns")
        hbar = ttk.Scrollbar(pane, orient="horizontal", command=self.cmd_out.xview)
        hbar.grid(row=2, column=0, sticky="ew")
        self.cmd_out.configure(yscrollcommand=bar.set, xscrollcommand=hbar.set,
                               state="disabled")

    # ==================================================================
    # 状态
    # ==================================================================

    def _refresh_mode(self) -> None:
        # 切主题会销毁重建整个界面，延迟调用的这个方法可能落到旧实例上
        try:
            if not self.mode_label.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            cfg = self._cfg()
        except Exception:  # noqa: BLE001
            self.mode_label.configure(text="AI 模块不可用")
            return

        if cfg.key_broken:
            text = "密钥无法解密（换过机器或用户），请点「设置」重新填写"
        elif cfg.ready():
            text = f"在线模式 · {cfg.model}"
        else:
            text = "纯离线模式 · 点右上角「设置」填密钥就能用，其它页签不受影响"

        self.mode_label.configure(text=text)

    def _status(self, text: str) -> None:
        self.intent_status.configure(text=text)

    # ==================================================================
    # 目标厂商
    # ==================================================================

    def sync_vendor(self) -> None:
        """主页面切厂商时，这里跟着变。

        AI 页选的是「这次要生成哪家的命令」，默认就是主页面当前那家。
        主页面变了这里跟着变，免得用户在主页选了华为、回 AI 页还是按 H3C
        抽参数——端口编号习惯不一样，端到端会错。
        """
        try:
            if not self.vendor_box.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self.vendor_box.set(get_vendor(self.app.vendor_key).name)
        except ValueError:
            pass

    def _target_vendor(self) -> str:
        """当前选中的生成厂商。名字对不上就退回主页面那个。"""
        name = self.vendor_box.get()
        for vendor in VENDORS:
            if vendor.name == name:
                return vendor.key
        return self.app.vendor_key

    # ==================================================================
    # 线程
    # ==================================================================

    def _start(self, worker) -> None:
        if self._busy:
            return
        self._busy = True
        self._chunk_count = 0
        self.intent_btn.state(["disabled"])
        self._status("正在解析…")

        def run():
            try:
                self._queue.put((worker(), None))
            except Exception as exc:  # noqa: BLE001 —— worker 里任何异常都要回到主线程
                self._queue.put((None, exc))

        threading.Thread(target=run, daemon=True).start()
        self.app.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        # 切主题会把这个页面整个销毁重建，旧的轮询得自己退场
        try:
            if not self.intent_status.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            payload, exc = self._queue.get_nowait()
        except queue.Empty:
            if self._busy:
                self.app.after(POLL_MS, self._poll)
            return

        self._busy = False
        self.intent_btn.state(["!disabled"])
        if exc is not None:
            self._status(f"失败：{exc}")
            self._write_intent(f"失败：{exc}", "err")
            return
        self._after_intent(payload)

    def _on_chunk(self, piece: str) -> None:
        """worker 线程里被调用。只累加计数，不碰控件。"""
        self._chunk_count += 1

    # ==================================================================
    # 解析与回填
    # ==================================================================

    def _run_intent(self) -> None:
        if self._busy:
            return
        text = self.intent_input.get("1.0", "end").strip()
        if not text:
            self._status("先写一句要做什么")
            return

        try:
            cfg = self._cfg()
        except Exception as exc:  # noqa: BLE001
            self._status(f"失败：{exc}")
            return

        if not cfg.ready():
            self._clear()
            self._write_intent("这一页要联网才能用，还没配置接口和密钥。", "warn")
            self._write_intent("", "body")
            self._write_intent("点右上角「设置」填好接口地址、模型名和密钥。地址填 "
                               "OpenAI 兼容的就行，DeepSeek、通义、智谱、本地 ollama "
                               "都能用。", "body")
            self._write_intent("", "body")
            self._write_intent("不配置也不影响使用：参数可以手填，命令照常生成，"
                               "「配置体检」里的安全体检和语法校对也都能跑。", "dim")
            return

        mods = self._ai()
        # 按「生成厂商」下拉里选的那家抽参数 —— 端口编号习惯各家不同，
        # 拿 H3C 的 1/0/1 去生成华为命令，端口就是错的
        vendor_key = self._target_vendor()

        def worker():
            return mods["intent"].resolve(text, vendor_key, cfg,
                                          on_chunk=self._on_chunk)

        self._start(worker)

    def _after_intent(self, result) -> None:
        self._clear()

        if result.error:
            self._write_intent(f"没解析出来：{result.error}", "err")
            self._status("解析失败")
            return

        scenario = result.scenario_obj()
        name = scenario.name if scenario else result.scenario
        pct = int(result.confidence * 100)
        target = self._target_vendor()
        self._write_intent(
            f"场景：{name}（把握 {pct}%，按 {get_vendor(target).name} 生成）", "h1")

        if result.params:
            # 只有目标厂商就是主页面当前厂商时，才拿界面现值做「原来是 X」的比较；
            # 不是同一家的话，那份现值根本不是准备下发的那套参数
            same_vendor = target == self.app.vendor_key
            current = self.app._collect(scenario) if (scenario and same_vendor) else {}
            for key, value in result.params.items():
                label = key
                if scenario:
                    param = scenario.param_map().get(key)
                    if param is not None:
                        label = param.label
                changed = str(current.get(key, "")) != str(value)
                line = f"  {label} = {value}"
                if changed and str(current.get(key, "")).strip():
                    line += f"　（原来是 {current.get(key)}）"
                self._write_intent(line, "body")
        else:
            self._write_intent("  没有抽到明确的参数", "warn")

        if result.missing:
            names = []
            for key in result.missing:
                param = scenario.param_map().get(key) if scenario else None
                names.append(param.label if param else key)
            self._write_intent("")
            self._write_intent("未确定：" + "、".join(names), "warn")

        if result.question:
            self._write_intent(f"问一句：{result.question}", "body")
        if result.note:
            self._write_intent(result.note, "dim")
        if result.dropped:
            self._write_intent("")
            self._write_intent("已丢弃（不属于这个场景或取值不合法）：", "dim")
            for item in result.dropped:
                self._write_intent("  " + item, "dim")

        self._status(f"解析完成（{self._chunk_count} 段）")

        if not scenario:
            return
        if not supports(target, result.scenario):
            self._write_intent("")
            self._write_intent(
                f"{get_vendor(target).name} 不支持这个场景。"
                f"换个厂商试试，或者把参数手填上去。", "warn")
            return

        # 只有参数确实缺的时候才停下来问。齐了就直接填、直接生成——
        # 多一步确认对熟手来说纯属损耗，对新手也不解决问题
        if result.needs_confirm():
            self._status("有参数没定下来，补一句再解析，或者直接点右边按钮按默认值生成")
            self.intent_btn.configure(text="按现有参数生成",
                                      command=lambda: self._apply_intent(result))
            return

        self._apply_intent(result)

    def _apply_intent(self, result) -> None:
        scenario = result.scenario_obj()
        if scenario is None:
            self._status("场景不存在")
            return

        app = self.app
        key = result.scenario
        target = self._target_vendor()

        # 目标厂商跟主页面不一致时，先切过去再写参数。
        # 顺序不能反：_switch_vendor 会用目标厂商自己的那份参数重填界面，
        # 先写后切的话会被它覆盖掉
        if target != app.vendor_key:
            app._save_fields()
            app._switch_vendor(target)

        # 取界面现值，再用 AI 的结果覆盖 —— 用户在表单里手填的
        # 其他参数不能被这次回填冲掉
        merged = dict(app._collect(scenario))
        merged.update(result.params)
        app.store[(target, key)] = merged

        app._restore_fields()
        ok = app._generate(key)

        self.intent_btn.configure(text="解析并填入表单", command=self._run_intent)
        if hasattr(app, "_scroll_to_card"):
            app._scroll_to_card(key)
        if hasattr(app, "_flash_card"):
            app._flash_card(key)

        if ok:
            vendor_name = get_vendor(target).name
            self._show_commands(key, target)
            self._status(f"已按 {vendor_name} 生成，命令在下面")
            self._write_intent("")
            self._write_intent(
                f"已按 {vendor_name} 生成，命令在下方，可以直接复制。", "ok")
        else:
            self._clear_commands()
            reason = app.errors.get(key, "生成失败")
            self._status(f"参数已填入，但没生成出来：{reason}")
            self._write_intent("")
            self._write_intent(f"参数填进去了，但生成报错：{reason}", "err")
            self._write_intent("多半是某个参数取值不合适，改一下再试。", "dim")

    # ------------------------------------------------------------------
    # 命令区
    # ------------------------------------------------------------------

    def _show_commands(self, scenario_key: str, vendor_key: str) -> None:
        """把刚生成的这一段命令贴到底下的命令区。

        只用 ``to_text(comments=False)``：分节标题、说明、风险提示全部去掉，
        得到的是能直接整段粘到设备上的东西。要带注释的完整版在主页面。
        """
        result = self.app.results.get(scenario_key)
        if result is None:
            self._clear_commands()
            return

        try:
            text = result.to_text(header=False, comments=False)
        except Exception:  # noqa: BLE001
            text = ""

        self._local_text = text
        self._peer_text = ""
        self._peer_label = ""
        # 留一份上下文：「复制到对端」要用同一份参数重算，不能从命令区反推
        self._pair_ctx = (scenario_key,
                          dict(self.app.store.get((vendor_key, scenario_key), {})),
                          vendor_key)
        self._render_commands()
        self._refresh_peer_btn()

    def _render_commands(self) -> None:
        body = self._local_text
        if self._peer_text:
            sep = f"──────── 对端 {self._peer_label} ────────"
            body = f"{self._local_text}\n\n{sep}\n{self._peer_text}"

        self.cmd_out.configure(state="normal")
        self.cmd_out.delete("1.0", "end")
        self.cmd_out.insert("1.0", body)
        self.cmd_out.configure(state="disabled")

        if self._local_text.strip():
            vendor = self._pair_ctx[2] if self._pair_ctx else ""
            try:
                vendor_name = get_vendor(vendor).name
            except ValueError:
                vendor_name = ""
            lines = len(self._local_text.splitlines())
            extra = "　+ 对端" if self._peer_text else ""
            self.cmd_meta.configure(
                text=f"{vendor_name} · {lines} 行{extra}".strip(" ·"))
        else:
            self.cmd_meta.configure(text="")

        self.copy_peer_btn.state(["!disabled"] if self._peer_text
                                 else ["disabled"])

    def _clear_commands(self) -> None:
        self._local_text = ""
        self._peer_text = ""
        self._peer_label = ""
        self._pair_ctx = None
        self.cmd_out.configure(state="normal")
        self.cmd_out.delete("1.0", "end")
        self.cmd_out.configure(state="disabled")
        self.cmd_meta.configure(text="")
        self.peer_btn.state(["disabled"])
        self.copy_peer_btn.state(["disabled"])

    # ------------------------------------------------------------------
    # 复制
    # ------------------------------------------------------------------

    def _copy_local(self) -> None:
        self._copy_text(self._local_text, "本端")

    def _copy_peer(self) -> None:
        self._copy_text(self._peer_text, "对端")

    def _copy_text(self, text: str, which: str) -> None:
        if not text.strip():
            self._status(f"还没有{which}命令可复制")
            return
        self.app.clipboard_clear()
        self.app.clipboard_append(text)
        self._status(f"已复制{which} {len(text.splitlines())} 行")

    # ------------------------------------------------------------------
    # 复制到对端
    # ------------------------------------------------------------------

    def _refresh_peer_btn(self) -> None:
        """有端口类参数才允许「复制到对端」——syslog、NTP 这类没有端口可换。"""
        enabled = False
        if self._pair_ctx is not None:
            scenario_key, params, _vendor = self._pair_ctx
            try:
                scenario = registry.get(scenario_key)
                enabled = _primary_port_param(scenario, params) is not None
            except Exception:  # noqa: BLE001
                enabled = False
        self.peer_btn.state(["!disabled"] if enabled else ["disabled"])

    def _open_peer(self) -> None:
        if self._pair_ctx is None:
            self._status("先生成一段命令")
            return
        scenario_key, params, vendor_key = self._pair_ctx
        scenario = registry.get(scenario_key)
        found = _primary_port_param(scenario, params)
        if found is None:
            self._status("这个场景没有端口参数，不需要对端")
            return
        PeerDialog(self, scenario, params, found)

    def apply_peer(self, peer_params: dict, label: str) -> bool:
        """用对端端口重跑一次同一场景，把结果并列显示在本端下面。

        只换端口，其余参数一律照搬——这正是「两端必须一致」的那部分。
        端口之外的东西（PVID、放通列表、阈值）本来就该两端相同，
        不做成可分别修改，免得留下「一端改了另一端忘了」的口子。
        """
        if self._pair_ctx is None:
            return False

        scenario_key, _params, vendor_key = self._pair_ctx
        scenario = registry.get(scenario_key)

        try:
            from ..vendors import generate
            result = generate(vendor_key, scenario_key, peer_params, scenario)
            peer_text = result.to_text(header=False, comments=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("对端命令没生成出来",
                                 f"{exc}\n\n检查一下对端端口名是否符合"
                                 f"{get_vendor(vendor_key).name} 的写法。",
                                 parent=self.app)
            return False

        self._peer_text = peer_text
        self._peer_label = label
        self._render_commands()
        self._status(f"已生成对端（{label}），可用「复制对端」单独取走")
        return True

    def _goto_main(self) -> None:
        self.app.pages.select(self.app.page_main)

    # ==================================================================
    # 从现网配置导入
    # ==================================================================

    def _open_import(self) -> None:
        ImportDialog(self.app, self)

    def apply_snapshot(self, snap, scenario_key: str, params: dict) -> bool:
        """把现网解析出来的建议值填进表单并生成。

        厂商以**识别结果**为准：配置是 H3C 的语法，端口名就是 ``1/0/1``，
        填到华为的表单里必然对不上。识别不出才退回当前厂商。
        """
        app = self.app
        scenario = registry.get(scenario_key)
        target = snap.vendor or app.vendor_key

        # 留一份给变更包用：导出时「变更前状态」那段就靠它
        app._imported_snap = snap

        if not supports(target, scenario_key):
            self._status(f"{get_vendor(target).name} 不支持这个场景")
            return False

        if target != app.vendor_key:
            app._save_fields()
            app._switch_vendor(target)

        # 先取界面现值再覆盖：用户手填的其他参数不能被冲掉
        merged = dict(app._collect(scenario))
        merged.update(params)
        app.store[(target, scenario_key)] = merged

        app._restore_fields()
        ok = app._generate(scenario_key)

        self._clear()
        vendor_name = get_vendor(target).name
        if ok:
            self._show_commands(scenario_key, target)
            self._status(f"已从现网导入 {len(params)} 个字段，按 {vendor_name} 生成")
            self._write_intent(
                f"已按现网配置填入 {len(params)} 个字段，按 {vendor_name} 生成。", "ok")
            self._write_intent("", "body")
            for key, value in params.items():
                param = scenario.param_map().get(key)
                label = param.label if param else key
                self._write_intent(f"  {label} = {value}", "body")
            self._write_intent("", "body")
            self._write_intent("这些是现网现状，改完要下发的那几项再点生成。", "dim")
        else:
            self._clear_commands()
            reason = app.errors.get(scenario_key, "生成失败")
            self._status(f"导入成功，但没生成出来：{reason}")
            self._write_intent(f"字段填进去了，但生成报错：{reason}", "err")
        return ok

    # ------------------------------------------------------------------
    # 文本写入
    # ------------------------------------------------------------------

    def _write_intent(self, text: str, tag: str = "body") -> None:
        self.intent_out.configure(state="normal")
        self.intent_out.insert("end", text + "\n", tag)
        self.intent_out.see("end")
        self.intent_out.configure(state="disabled")

    def _clear(self) -> None:
        self.intent_out.configure(state="normal")
        self.intent_out.delete("1.0", "end")
        self.intent_out.configure(state="disabled")
        self._clear_commands()

    # ==================================================================
    # 设置
    # ==================================================================

    def _open_settings(self) -> None:
        try:
            cfg = self._cfg()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("AI 设置", str(exc), parent=self.app)
            return
        SettingsDialog(self.app, cfg, self.reload_config)


class SettingsDialog(tk.Toplevel):
    """接口地址 / 模型 / 密钥。密钥用 DPAPI 加密后落到 %APPDATA%。"""

    def __init__(self, master, cfg, on_saved):
        super().__init__(master)
        self.cfg = cfg
        self.on_saved = on_saved
        self.configure(background=T.BG)
        self.title("AI 设置")
        self.resizable(False, False)
        self.transient(master)

        self._queue: queue.Queue = queue.Queue()
        self._busy = False

        self._build()
        self._center(master)
        self.grab_set()

    def _center(self, master) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw, mh = master.winfo_width(), master.winfo_height()
        self.geometry(f"+{mx + (mw - w) // 2}+{my + (mh - h) // 3}")

    def _build(self) -> None:
        box = tk.Frame(self, background=T.BG, padx=T.px(18), pady=T.px(14))
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)

        rows = (
            ("接口地址", "base_url",
             "OpenAI 兼容的地址。DeepSeek 用 https://api.deepseek.com/v1，"
             "本地 ollama 用 http://localhost:11434/v1"),
            ("模型名", "model", "如 deepseek-chat、qwen-plus、llama3.1"),
        )
        self.vars = {}
        for i, (label, key, hint) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=i * 2, column=0, sticky="w",
                                            pady=(0, T.px(2)))
            var = tk.StringVar(value=getattr(self.cfg, key, ""))
            self.vars[key] = var
            ttk.Entry(box, textvariable=var, width=46).grid(
                row=i * 2, column=1, sticky="ew", padx=(T.px(10), 0))
            ttk.Label(box, text=hint, style="Faint.TLabel").grid(
                row=i * 2 + 1, column=1, sticky="w", padx=(T.px(10), 0),
                pady=(0, T.px(8)))

        base = len(rows) * 2
        ttk.Label(box, text="密钥").grid(row=base, column=0, sticky="w")
        self.key_var = tk.StringVar(value="")
        self.key_entry = ttk.Entry(box, textvariable=self.key_var, width=46,
                                   show="•")
        self.key_entry.grid(row=base, column=1, sticky="ew", padx=(T.px(10), 0))

        hint = ("已保存密钥：留空表示不修改。" if self.cfg.key
                else "还没有密钥。填了才能用在线功能。")
        if self.cfg.key_broken:
            hint = "原来的密钥解不开了（换过机器或用户），请重新填。"

        # 这条得写明白：用户会担心「我填了密钥，这程序会不会带着它跑」，
        # 或者把 exe 发给同事时怕连自己的密钥一起发出去
        hint += ("\n存在本机当前 Windows 用户下，不写进程序和方案文件。"
                 "换电脑或换账户要重填。")
        ttk.Label(box, text=hint, style="Faint.TLabel", justify="left",
                  wraplength=T.px(520)).grid(
            row=base + 1, column=1, sticky="w", padx=(T.px(10), 0),
            pady=(0, T.px(8)))

        opts = tk.Frame(box, background=T.BG)
        opts.grid(row=base + 2, column=0, columnspan=2, sticky="ew",
                  pady=(T.px(4), 0))

        # 这里原来有个「启用在线功能」勾选框，默认没勾，导致用户填完密钥、
        # 测试通过，主界面还显示纯离线。去掉——填了密钥就是能用。
        self.redact_var = tk.BooleanVar(value=self.cfg.redact)
        ttk.Checkbutton(opts, text="发送前脱敏", variable=self.redact_var).pack(
            side="left")

        self.stream_var = tk.BooleanVar(value=self.cfg.stream)
        ttk.Checkbutton(opts, text="流式显示", variable=self.stream_var).pack(
            side="left", padx=(T.px(12), 0))

        ttk.Label(box, text="超时").grid(row=base + 3, column=0, sticky="w",
                                        pady=(T.px(10), 0))
        self.timeout_var = tk.StringVar(value=str(self.cfg.timeout))
        ttk.Entry(box, textvariable=self.timeout_var, width=8).grid(
            row=base + 3, column=1, sticky="w", padx=(T.px(10), 0),
            pady=(T.px(10), 0))

        self.status = ttk.Label(box, text="", style="Faint.TLabel",
                                wraplength=T.px(460))
        self.status.grid(row=base + 4, column=0, columnspan=2, sticky="w",
                         pady=(T.px(10), 0))

        foot = tk.Frame(box, background=T.BG)
        foot.grid(row=base + 5, column=0, columnspan=2, sticky="e",
                  pady=(T.px(12), 0))

        self.test_btn = ttk.Button(foot, text="测试连接", style="Ghost.TButton",
                                   command=self._test)
        self.test_btn.pack(side="left", padx=(0, T.px(8)))
        ttk.Button(foot, text="清除密钥", style="Ghost.TButton",
                   command=self._clear_key).pack(side="left", padx=(0, T.px(8)))
        ttk.Button(foot, text="取消", command=self.destroy).pack(
            side="left", padx=(0, T.px(8)))
        ttk.Button(foot, text="保存", style="Accent.TButton",
                   command=self._save).pack(side="left")

    # ------------------------------------------------------------------

    def _collect(self):
        from ..ai import config as aicfg

        return aicfg.AIConfig(
            base_url=self.vars["base_url"].get().strip(),
            model=self.vars["model"].get().strip(),
            # 留空 = 不修改已保存的密钥。只想改模型名时不用重粘密钥
            key=self.key_var.get().strip() or self.cfg.key,
            timeout=self._timeout(),
            redact=self.redact_var.get(),
            stream=self.stream_var.get(),
        )

    def _timeout(self) -> int:
        try:
            value = int(self.timeout_var.get())
        except (TypeError, ValueError):
            return 15
        return max(3, min(120, value))

    def _save(self) -> bool:
        from ..ai import config as aicfg

        cfg = self._collect()
        if not cfg.key:
            messagebox.showwarning("缺少密钥",
                                   "密钥是空的，填上才能用在线功能。\n\n"
                                   "不用在线功能的话直接关掉这个窗口就行，"
                                   "其它页签完全不受影响。",
                                   parent=self)
            return False
        if not cfg.base_url.startswith(("http://", "https://")):
            messagebox.showwarning("地址不合法",
                                   "接口地址要以 http:// 或 https:// 开头。",
                                   parent=self)
            return False
        try:
            aicfg.save(cfg)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", str(exc), parent=self)
            return False

        self.cfg.base_url = cfg.base_url
        self.cfg.model = cfg.model
        self.cfg.key = cfg.key
        self.cfg.timeout = cfg.timeout
        self.cfg.redact = cfg.redact
        self.cfg.stream = cfg.stream
        self.cfg.key_broken = False
        if self.on_saved:
            self.on_saved()
        self.destroy()
        return True

    def _clear_key(self) -> None:
        if not messagebox.askyesno("清除密钥", "确定要清除保存的密钥吗？",
                                   parent=self):
            return
        from ..ai import config as aicfg
        aicfg.clear_key(self.cfg)
        self.key_var.set("")
        self.status.configure(text="密钥已清除")
        if self.on_saved:
            self.on_saved()

    def _test(self) -> None:
        if self._busy:
            return
        from ..ai import provider

        cfg = self._collect()
        if not cfg.key:
            self.status.configure(text="先填密钥再测试")
            return

        self._busy = True
        self.test_btn.state(["disabled"])
        self.status.configure(text="正在测试…")

        def run():
            try:
                ok, message = provider.probe(cfg)
            except Exception as exc:  # noqa: BLE001
                ok, message = False, f"测试失败：{exc}"
            self._queue.put((ok, message))

        threading.Thread(target=run, daemon=True).start()
        self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        # 窗口可能已经被关掉了，这时候再碰控件会 TclError
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            ok, message = self._queue.get_nowait()
        except queue.Empty:
            self.after(POLL_MS, self._poll)
            return
        self._busy = False
        self.test_btn.state(["!disabled"])
        self.status.configure(text=("✓ " if ok else "✗ ") + message)


class ImportDialog(tk.Toplevel):
    """从现网配置导入参数。

    流程是「粘贴 → 解析 → 看摘要 → 选场景 → 填入」。解析纯本地、瞬间完成，
    不上传也不改任何配置，只是把人本来要去设备上一条条看的东西读成字段。

    为什么不做成自动匹配场景
    ----------------------
    一份配置里同时有 VLAN、STP、ACL、OSPF，工具猜不出你这次要改哪块。
    让人选一个场景，比猜错一屏参数强。
    """

    def __init__(self, master, page):
        super().__init__(master)
        self.app = master
        self.page = page
        self.snap = None
        self.configure(background=T.BG)
        self.title("从现网配置导入")
        self.geometry(f"{T.px(800)}x{T.px(680)}")
        self.transient(master)
        self._build()
        self._center(master)
        self.grab_set()

    def _center(self, master) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw, mh = master.winfo_width(), master.winfo_height()
        self.geometry(f"+{mx + (mw - w) // 2}+{my + (mh - h) // 3}")

    def _build(self) -> None:
        box = tk.Frame(self, background=T.BG, padx=T.px(16), pady=T.px(14))
        box.pack(fill="both", expand=True)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=3)
        box.rowconfigure(3, weight=2)

        head = tk.Frame(box, background=T.BG)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="把设备上的配置整段粘进来",
                  style="CardTitle.TLabel").pack(side="left")
        ttk.Label(head, text="display current-configuration / show running-config",
                  style="Faint.TLabel").pack(side="left", padx=(T.px(10), 0))

        # ---- 粘贴区 --------------------------------------------------
        wrap = tk.Frame(box, background=T.BG)
        wrap.grid(row=1, column=0, sticky="nsew", pady=(T.px(6), T.px(6)))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.input = tk.Text(wrap, background=T.FIELD, foreground=T.C_CMD,
                             insertbackground=T.FG, font=T.FONT_MONO,
                             relief="flat", bd=0, wrap="none",
                             padx=T.px(10), pady=T.px(8),
                             selectbackground=T.SEL_BG, selectforeground=T.FG)
        self.input.grid(row=0, column=0, sticky="nsew")
        vb = ttk.Scrollbar(wrap, orient="vertical", command=self.input.yview)
        vb.grid(row=0, column=1, sticky="ns")
        hb = ttk.Scrollbar(wrap, orient="horizontal", command=self.input.xview)
        hb.grid(row=1, column=0, sticky="ew")
        self.input.configure(yscrollcommand=vb.set, xscrollcommand=hb.set)
        self.input.bind("<Control-Return>", lambda e: self._parse() or "break")

        mid = tk.Frame(box, background=T.BG)
        mid.grid(row=2, column=0, sticky="ew")
        ttk.Button(mid, text="解析", style="Accent.TButton",
                   command=self._parse).pack(side="left")
        self.hint = ttk.Label(mid, text="只在本机解析，不上传", style="Faint.TLabel")
        self.hint.pack(side="left", padx=(T.px(10), 0))

        # ---- 摘要 ----------------------------------------------------
        self.summary = tk.Text(box, background=T.PANEL, foreground=T.FG,
                               font=T.FONT_UI, relief="flat", bd=0, wrap="word",
                               padx=T.px(12), pady=T.px(10),
                               selectbackground=T.SEL_BG, selectforeground=T.FG)
        self.summary.grid(row=3, column=0, sticky="nsew")
        self.summary.tag_configure("body", foreground=T.FG)
        self.summary.tag_configure("dim", foreground=T.FG_FAINT)
        self.summary.tag_configure("warn", foreground=T.C_WARN)
        self.summary.configure(state="disabled")
        self._set_summary(["粘好配置后点「解析」，这里会列出读到的厂商、VLAN、"
                           "Trunk 口和管理地址。"], "dim")

        # ---- 底部 ----------------------------------------------------
        foot = tk.Frame(box, background=T.BG)
        foot.grid(row=4, column=0, sticky="ew", pady=(T.px(10), 0))
        foot.columnconfigure(2, weight=1)

        ttk.Label(foot, text="填到哪个场景").grid(row=0, column=0, sticky="w")
        self.scene_box = ttk.Combobox(foot, state="readonly", width=28,
                                      font=T.FONT_UI)
        self.scene_box["values"] = [s.name for s in registry.SCENARIOS]
        self.scene_box.set(registry.get("vlan_port").name)
        self.scene_box.grid(row=0, column=1, sticky="w", padx=(T.px(8), 0))

        ttk.Button(foot, text="关闭", command=self.destroy).grid(
            row=0, column=3, sticky="e", padx=(T.px(8), 0))
        self.apply_btn = ttk.Button(foot, text="填入表单", style="Gen.TButton",
                                    command=self._apply, state="disabled")
        self.apply_btn.grid(row=0, column=4, sticky="e")

    # ------------------------------------------------------------------

    def _set_summary(self, lines: list, tag: str = "body") -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        for line in lines:
            self.summary.insert("end", line + "\n", tag)
        self.summary.configure(state="disabled")

    def _parse(self) -> None:
        text = self.input.get("1.0", "end").strip()
        if not text:
            self.hint.configure(text="先把配置粘进来")
            return

        self.snap = snapshot.parse(text)
        lines = snapshot.summary_lines(self.snap)

        if not self.snap.vlans and not self.snap.ifaces:
            self._set_summary(lines + ["", "没读出 VLAN 也没读出接口。"
                                            "确认粘的是完整的配置输出，不是只截了几行。"],
                              "warn")
            self.hint.configure(text="没读到东西")
            self.apply_btn.state(["disabled"])
            return

        self._set_summary(lines)
        self.hint.configure(
            text=f"读出 {len(self.snap.vlans)} 个 VLAN、"
                 f"{len(self.snap.ifaces)} 个接口")
        self.apply_btn.state(["!disabled"])

        # 有 trunk 口就默认 VLAN 场景，这是最常见的操作
        if self.snap.trunk_ifaces():
            self.scene_box.set(registry.get("vlan_port").name)

    def _scenario_key(self) -> str:
        name = self.scene_box.get()
        for scenario in registry.SCENARIOS:
            if scenario.name == name:
                return scenario.key
        return "vlan_port"

    def _apply(self) -> None:
        if self.snap is None:
            return

        key = self._scenario_key()
        vendor = self.snap.vendor or self.app.vendor_key
        params = snapshot.suggest(vendor, key, self.snap)

        if not params:
            scenario = registry.get(key)
            messagebox.showinfo(
                "这个场景没有可导入的字段",
                f"这份配置里没有能对上「{scenario.name}」的东西。\n\n"
                f"现网导入只覆盖能从现状直接反推的场景——VLAN 划分、管理面、"
                f"静态路由、端口隔离。像 BGP、MPLS、堆叠这类，"
                f"现状里没有能直接当参数用的字段。",
                parent=self)
            return

        self.page.apply_snapshot(self.snap, key, params)
        self.destroy()


class PeerDialog(tk.Toplevel):
    """把本端参数照搬一份，只换端口，生成对端设备的命令。

    只换端口是有意的
    ---------------
    trunk 两端、聚合两端、VRRP 两端的共同点是「除了口，别的都该一样」。
    如果允许分别改放通列表、PVID、阈值，就给了「一端改了另一端忘了」的机会，
    而这正是两端配置最常见的错法。

    所以这里只有一个输入框：对端用哪个口。其余参数原样搬过去，
    要改就回主页面改，一改两边都变。
    """

    def __init__(self, page, scenario, params: dict, port_param):
        super().__init__(page.app)
        self.page = page
        self.scenario = scenario
        self.params = dict(params)
        self.port_key, self.port_label, self.port_value = port_param

        self.configure(background=T.BG)
        self.title("复制到对端")
        self.resizable(False, False)
        self.transient(page.app)
        self._build()
        self._center(page.app)
        self.grab_set()

    def _center(self, master) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw, mh = master.winfo_width(), master.winfo_height()
        self.geometry(f"+{mx + (mw - w) // 2}+{my + (mh - h) // 3}")

    def _build(self) -> None:
        box = tk.Frame(self, background=T.BG, padx=T.px(18), pady=T.px(14))
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text=f"场景：{self.scenario.name}",
                  style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2,
                                                 sticky="w")
        ttk.Label(box, text="其余参数原样搬过去，只换端口——两端不一致大多出在这里。",
                  style="Faint.TLabel").grid(row=1, column=0, columnspan=2,
                                             sticky="w", pady=(T.px(4), T.px(12)))

        ttk.Label(box, text=f"本端{self.port_label}").grid(row=2, column=0,
                                                          sticky="w")
        ttk.Label(box, text=self.port_value or "（空）",
                  style="CardTitle.TLabel").grid(row=2, column=1, sticky="w",
                                                 padx=(T.px(12), 0))

        ttk.Label(box, text=f"对端{self.port_label}").grid(row=3, column=0,
                                                          sticky="w",
                                                          pady=(T.px(10), 0))
        self.value_var = tk.StringVar()
        entry = ttk.Entry(box, textvariable=self.value_var, width=32)
        entry.grid(row=3, column=1, sticky="ew", padx=(T.px(12), 0),
                   pady=(T.px(10), 0))
        entry.bind("<Return>", lambda e: self._ok())
        entry.focus_set()

        self.status = ttk.Label(box, text="", style="Faint.TLabel")
        self.status.grid(row=4, column=0, columnspan=2, sticky="w",
                         pady=(T.px(8), 0))

        foot = tk.Frame(box, background=T.BG)
        foot.grid(row=5, column=0, columnspan=2, sticky="e", pady=(T.px(12), 0))
        ttk.Button(foot, text="取消", command=self.destroy).pack(
            side="left", padx=(0, T.px(8)))
        ttk.Button(foot, text="生成对端命令", style="Accent.TButton",
                   command=self._ok).pack(side="left")

    def _ok(self) -> None:
        value = self.value_var.get().strip()
        if not value:
            self.status.configure(text="填一下对端用哪个端口")
            return
        if value == (self.port_value or "").strip():
            self.status.configure(text="跟本端是同一个口，确认一下再填")
            return

        peer = dict(self.params)
        peer[self.port_key] = value
        if self.page.apply_peer(peer, value):
            self.destroy()
