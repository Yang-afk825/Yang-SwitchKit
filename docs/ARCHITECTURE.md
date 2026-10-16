# Yang-SwitchKit 架构说明

> 这份文档回答三个问题：**每个模块负责什么**、**模块之间怎么联动**、
> **为什么这么设计**。想看怎么用请回到 [README](../README.md)。

- 代码规模：约 **9600 行**（含注释与文档字符串）
- 覆盖：**4 厂商 × 22 场景 = 88 组**命令模板，**132 条**命令概念详解
- 运行形态：纯离线的 Windows 桌面程序，Python 3.12 + Tkinter，打包后 11 MB 单文件

---

## 一、一句话定位

把「配一台交换机」拆成 22 个常见场景，用户选厂商、填参数，
程序产出**可以直接粘贴到设备的配置命令**，并给出**逐条命令详解**与**四厂商命令对照**。

关键词是「**离线**」和「**正确**」：
不联网、不连设备、不下发；命令以各厂商官方手册为准逐条核对，
凡随版本变化的写法都显式标注并提示用 `?` 确认。

---

## 二、整体架构

### 2.1 三层结构

```
┌─────────────────────────────────────────────────────────────────────┐
│  界面层  scg/ui/                                                     │
│  ┌───────────────┐  ┌───────────────┐  ┌──────────┐  ┌───────────┐  │
│  │ window.py     │  │ theme.py      │  │ dpi.py   │  │ __init__  │  │
│  │ 主窗口 / 交互 │  │ 调色板 + ttk  │  │ DPI 适配 │  │           │  │
│  └───────┬───────┘  └───────────────┘  └──────────┘  └───────────┘  │
└──────────┼──────────────────────────────────────────────────────────┘
           │  只调用下面三个「服务层」模块，自己不拼命令、不写正则
┌──────────▼──────────────────────────────────────────────────────────┐
│  服务层                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │ registry.py │  │ vendors/    │  │ explain.py  │                  │
│  │ 场景=参数   │  │ 命令生成    │  │ 命令详解    │                  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                  │
│         │                │                │                         │
│  ┌──────▼────────────────▼────────────────▼──────┐                  │
│  │ model.py（数据结构）  ports.py（端口解析）    │                  │
│  │ base.py（构建器 + 共用解析工具）              │                  │
│  │ verify.py（核验记录，纯数据）                 │                  │
│  └───────────────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────┘
           ▲
┌──────────┴──────────────────────────────────────────────────────────┐
│  工具层  tools/          smoke_test.py  verify_exe.py  wincap.py ... │
│  构建层  build.py        PyInstaller 打包                            │
└─────────────────────────────────────────────────────────────────────┘
```

**分层的硬边界**：

| 层 | 允许做什么 | 绝对不做 |
|---|---|---|
| `ui/` | 画控件、收参数、把结果渲染成文本 | 不拼任何命令字符串、不写命令正则 |
| `vendors/` | 把参数字典变成命令列表 | 不认识 Tk，不 import 任何 UI |
| `explain.py` | 用正则认识「一行命令」并解释它 | 不生成命令，只解释命令 |
| `model.py` / `ports.py` / `base.py` | 提供数据结构与纯函数工具 | 不持有状态 |

这个边界的直接好处：`ui/` 整个目录删掉，命令库照样能跑（冒烟测试就是这么干的）。

### 2.2 一次「点生成」的数据流

```
用户在参数卡片里填值
        │
        ▼
 window.py  _collect(scenario)          把控件值读成 {"参数名": "值"}
        │
        ▼
 window.py  _generate(key)
        │
        ├──▶ vendors.supports(厂商, 场景)      这个厂商支持这个场景吗
        │
        ▼
 vendors.generate(厂商, 场景, 参数, 场景对象)
        │
        ▼
 vendors/h3c.py::_vlan_port(p, scenario)      ← 每个场景一个函数
        │
        │   用 base.Builder 逐段累积：
        │     b.block("创建 VLAN", lines, comment="...")
        │     b.note("提示")  /  b.warn("风险")
        │
        ▼
 model.GenResult { blocks=[], notes=[], warnings=[] }
        │
        ├──▶ window.py _script_text()    → 左侧脚本区的纯文本
        │
        └──▶ explain.render_explanation(厂商, result)
                     │
                     └─ 逐行 match() 到概念 → 详解页
```

关键点：**`GenResult` 是全局唯一的结果载体**。脚本区、详解页、导出、覆盖率统计
全都从同一个 `GenResult` 派生，不存在「界面显示的和导出的不一样」这种情况。

---

## 三、目录与模块清单

```
Yang-SwitchKit/
├── main.py                  20 行   启动入口
├── build.py                 77 行   PyInstaller 打包脚本
├── scg/                             核心包（Switch Config Generator）
│   ├── __init__.py           4 行   版本号
│   ├── model.py            137 行   数据模型：参数/场景/厂商/结果块
│   ├── ports.py            182 行   端口写法解析与展开
│   ├── registry.py         624 行   22 个场景的参数定义（唯一的「配置表」）
│   ├── explain.py         1847 行   132 条命令概念的详解知识库
│   ├── verify.py           157 行   正确性核验记录（纯数据）
│   ├── vendors/
│   │   ├── __init__.py      46 行   厂商聚合与分发
│   │   ├── base.py         415 行   结果构建器 + 各厂商共用的解析工具
│   │   ├── h3c.py         1106 行   H3C Comware V5/V7
│   │   ├── huawei.py      1039 行   华为 VRP V5/V8
│   │   ├── cisco.py       1061 行   思科 IOS 12.x/15.x
│   │   └── ruijie.py      1031 行   锐捷 RGOS 10.x/11.x
│   └── ui/
│       ├── __init__.py       4 行
│       ├── window.py      1087 行   主窗口：布局、交互、渲染
│       ├── theme.py        305 行   双主题调色板 + ttk 样式
│       └── dpi.py           46 行   高 DPI 适配
├── tools/
│   ├── smoke_test.py        61 行   4 × 22 全组合冒烟测试
│   ├── verify_exe.py       114 行   验证打包产物能启动（不截图）
│   ├── wincap.py           126 行   PrintWindow 抓窗口自身画面
│   ├── make_icon.py        106 行   用代码画 ico
│   └── shot.py              61 行   界面截图（开发自检用）
└── assets/
    └── app.ico                      图标（由 make_icon.py 生成）
```

---

## 四、逐模块详解

### 4.1 `main.py` —— 启动入口

只有 20 行，做两件事：

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 让源码方式运行也能 import scg
from scg.ui import window
window.run()
```

**为什么单独留一个入口文件**：PyInstaller 需要一个明确的入口脚本；
同时它也是唯一允许「导入 UI」的地方，工具脚本（`tools/`）全部绕开 UI，各测各的。

---

### 4.2 `scg/model.py` —— 数据模型

整个项目最底层的东西，**只有数据结构，没有一行厂商命令**。这样命令库能独立演进，
改命令不会波及界面。

| 类型 | 作用 | 关键点 |
|---|---|---|
| `Kind` | 参数控件类型常量 | `TEXT` / `NUM` / `CHOICE` / `BOOL` / `AREA` 五种 |
| `Param` | **一个可编辑参数** | `key` `label` `kind` `default` `choices` `help` `example` `span`。界面表单就是遍历它生成的 |
| `Scenario` | **一个配置场景** | `key` `name` `group` `summary` `params` `versions` `notes` `reboot` |
| `Vendor` | 一个厂商元信息 | `key` `name` `platform` `accent` `port_hint` |
| `Block` | 输出里被分隔开的一段 | `title` 段标题、`lines` 命令行、`comment` 段说明 |
| `GenResult` | **一次生成的完整结果** | `blocks` + `notes` + `warnings`，外加 `to_text()` |

`GenResult.to_text(comments=False)` 是「复制即用」的实现落点：只留命令行，
把所有 `#`（H3C/华为注释）和 `!`（思科/锐捷注释）开头的行滤掉。
`#` 和 `!` 在两家 CLI 里都不可能出现在命令开头，所以按首字符过滤是安全的。

**`Block` 的三段式设计**（标题 / 说明 / 命令）是整个输出格式的骨架：

```
# ---- 创建 VLAN ----          ← Block.title
# 这些 VLAN 会一次性建好        ← Block.comment
system-view                    ← Block.lines
vlan 10
```

界面上「保留注释」勾选框控制的，就是这些 `title` / `comment` / `notes` / `warnings`
要不要出现。

---

### 4.3 `scg/registry.py` —— 场景注册表（624 行）

**这是全项目唯一的「配置表」**：22 个 `Scenario(...)` 对象，每个声明自己的参数。

```python
Scenario(
    key="port_security",
    name="端口安全与 MAC 绑定",
    group="安全",
    summary="限制端口下能接入的 MAC 数量，并可把 MAC 固化绑定，防私接。",
    versions="Comware V5/V7 · VRP V5/V8 · IOS 12.x/15.x · RGOS 10.x/11.x",
    params=(
        Param("ports",   "受控端口",   Kind.TEXT, "1/0/1-8", span=2, help=PORT_HINT),
        Param("max_mac", "最大 MAC 数", Kind.NUM,  "3"),
        Param("action",  "违例动作",   Kind.CHOICE, "restrict",
              choices=(("restrict", "丢弃并告警 restrict"),
                       ("shutdown", "关闭端口 shutdown"),
                       ("protect",  "只丢弃不告警 protect"))),
        ...
    ),
    notes=("端口安全是全局开关 + 端口参数两级配置，缺一不可。", ...),
)
```

**联动**：这个文件是「一改三处」里最上游的一处 ——

- `ui/window.py` 遍历 `Scenario.params` **自动生成表单**，加参数完全不用改 UI
- `vendors/*.py::generate()` 收到 `scenario` 对象，把 `scenario.notes` 追加进结果
- `ui/window.py::_build_card()` 按 `Scenario.name` / `group` / `summary` 画卡片头

底部还导出三个聚合量：`SCENARIOS`（有序元组）、`BY_KEY`（按 key 查）、
`GROUPS`（分组去重保序，决定界面卡片顺序）。

---

### 4.4 `scg/ports.py` —— 端口写法解析（182 行）

把运维习惯的简写展开成各厂商的实际接口名。**这是全项目被调用最多的工具模块。**

支持四种写法：

| 输入 | 含义 |
|---|---|
| `1/0/1-8` | 1 到 8 号口 |
| `1/0/1-1/0/8` | 同上，另一种写法 |
| `1/0/1,1/0/3` | 两个不连续的口 |
| `XGE1/0/49` | 带类型前缀，十兆/万兆 |

两张映射表是核心：

```python
_ALIAS = {"xge": "ten-gigabit", "te": "ten-gigabit", ...}      # 用户写的 → 内部标识
_VENDOR_PORT_NAME = {                                          # 内部标识 → 厂商实际写法
    "h3c":    {"ten-gigabit": "Ten-GigabitEthernet", ...},
    "huawei": {"ten-gigabit": "XGigabitEthernet",    ...},      # 华为的万兆叫 XGE
    "cisco":  {"ten-gigabit": "TenGigabitEthernet",  ...},
    "ruijie": {"ten-gigabit": "TenGigabitEthernet",  ...},
}
_VENDOR_SEP = {"ruijie": " "}    # 锐捷的接口名与编号之间有空格：GigabitEthernet 0/1
```

两个对外函数：

- `expand(spec, vendor)` → `["GigabitEthernet1/0/1", ...]`，去重
- `sequential(ports)` → `(是否连续, 首个, 末个)`，连续时四个厂商模块会把它合并成
  `interface range X to Y`，几十行配置压成三行

**边界保护**：单次展开超过 512 个端口直接抛 `PortError`，防止手滑输入 `1/0/1-1/0/9999` 卡死界面。

---

### 4.5 `scg/vendors/base.py` —— 共用构建器与解析工具（415 行）

四家厂商模块都 `import base as B`，这里是「不要每家重写一遍」的那一层。

**（1）`Builder` —— 结果构建器**

```python
b = B.Builder("H3C 新华三", "VLAN 创建与端口划分")
b.block("创建 VLAN", lines, comment="说明文字")   # 加一段
b.note("提示")                                   # 加一条蓝字提示
b.warn("风险")                                   # 加一条红字风险
return b.done()                                  # 拿到 GenResult
```

`block()` 会自动丢掉空行，所以厂商函数可以放心地 `if 条件: 加一行`。

**（2）解析工具**（按用途分组）

| 组 | 函数 | 干什么 |
|---|---|---|
| 列表 | `split_list` `split_ifaces` | 按逗号/分号拆。**`split_ifaces` 专治「接口名自带空格」**，如 `Vlan-interface 10` |
| VLAN | `parse_vlans` `parse_vlan_names` `parse_pairs` `compact_vlans` | `10,20,30-32` → `[10,20,30,31,32]`；反向压成 `10-12,20` |
| 地址 | `parse_net` `prefix_to_dotted` `prefix_to_wildcard` `dotted_to_prefix` `expand_ip_range` | 掩码长度 ↔ 点分掩码 ↔ 反掩码 |
| 结构化 | `parse_routes` `parse_routes_area` `parse_acl_rules` | 多行文本输入（路由表、OSPF 网段、ACL 规则）解析成对象 |
| MAC | `parse_macs` `mac_dashed` `mac_dotted` `mac_for` | **按厂商给分组符**：H3C/华为连字符，思科/锐捷点 |
| 时区 | `parse_timezone` `h3c_timezone` `cisco_timezone` | 四家谁也不认 `UTC+08:00`，必须先解析再按家格式化 |
| 杂项 | `ensure` `truthy` `acl_is_advanced` | 必填校验、布尔判断、ACL 编号体系判断 |

**`acl_is_advanced(acl_id, vendor_key)` 是个典型例子**：H3C/华为的 2000-2999 是基础 ACL，
思科/锐捷的 100-199 是扩展 ACL —— 两套编号体系完全不同，必须传厂商进去才知道该按哪个判。

---

### 4.6 `scg/vendors/{h3c,huawei,cisco,ruijie}.py` —— 四厂商命令库（约 4200 行）

四个文件结构完全对称，每个约 1000 行：

```python
VENDOR = Vendor(key="h3c", name="H3C 新华三", platform="Comware V5 / V7",
                port_hint="接口编号形如 GigabitEthernet1/0/1；...")

_ENTER = "system-view"          # 进配置视图
_SAVE  = ("return", "save force")  # 保存并退出

def _if_blocks(port_list, body): ...   # 接口块；连续端口合并成 interface range

def _vlan_port(p, scenario): ...   # 22 个场景各一个函数
def _lag(p, scenario): ...
...
_HANDLERS = {"vlan_port": _vlan_port, "lag": _lag, ...}   # 场景 key → 处理函数

def generate(scenario_key, params, scenario):
    fn = _HANDLERS.get(scenario_key)
    if fn is None:
        raise ValueError(f"{VENDOR.name} 暂不支持场景：{scenario.name}")
    result = fn(params, scenario)
    for n in scenario.notes:          # registry 里的场景提示自动追加
        result.notes.append(n)
    return result
```

**每个处理函数的统一套路**：

```python
def _port_security(p, scenario):
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)   # ① 解析端口
    if not port_list:
        raise ValueError("请填写受控端口。")                    # ② 参数错就抛，界面会接住
    ...
    b.block("配置端口安全", [_ENTER] + _if_blocks(...), comment="...")  # ③ 累积
    ...
    b.block("保存配置", _SAVE)
    return b.done()
```

**厂商差异都收敛在这个目录里**，举几个真实对照：

| 同一件事 | H3C | 华为 | 思科 | 锐捷 |
|---|---|---|---|---|
| 进配置视图 | `system-view` | `system-view` | `configure terminal` | `configure terminal` |
| 保存 | `return` + `save force` | `return` + `save` | `end` + `write memory` | `end` + `write` |
| 端口隔离 | `port-isolate enable group N` | `port-isolate enable group N` | `switchport protected` | `switchport protected` |
| 风暴抑制 | `broadcast-suppression ratio 10` | `storm control broadcast min-rate percent 5 max-rate percent 10` | `storm-control broadcast level 10` | `storm-control broadcast level 10` |
| 环路检测 | `loopback-detection global enable` | `loop-detection enable` | **没有这个功能** | `loopback-detection enable` |
| 时区 | `clock timezone UTC add 08:00:00` | `clock timezone UTC add 08:00:00` | `clock timezone UTC 8` | `clock timezone UTC 8` |

思科那一格是「**没有**」而不是空白 —— 工具的做法是：**查不出把握的写法就不生成**，
改成注释说明 + 给出替代方案（PortFast + BPDU 保护 + errdisable 恢复），绝不假装四家都有。

---

### 4.7 `scg/explain.py` —— 命令详解知识库（1847 行）

**全项目最大的单文件**，也是最核心的设计。它解决一个矛盾：

> 命令是分厂商的（4 套写法），但知识是跨厂商的（「配 MSTP 根桥」这件事只有一件）。

**设计：一个概念（`Doc`）+ 四种写法（`Form`）**

```python
@dataclass(frozen=True)
class Form:
    pattern: str     # 匹配生成结果里某一行的正则
    syntax: str      # 语法原型，给用户看
    note: str = ""   # 版本 / 平台差异

@dataclass(frozen=True)
class Doc:
    title: str       # 概念名，如「端口隔离 / 保护口」
    group: str       # 分组，决定手册里的排列顺序
    desc: str        # 这个功能是干什么的
    params: tuple    # 参数含义
    caution: str     # 使用注意
    forms: dict      # 厂商 key → Form 或 Form 元组
```

一个概念长这样：

```python
D("时区", "运维",
  "设置设备所在时区，让日志和时钟显示本地时间。",
  forms={
      H: F(r"^clock timezone\s+", "clock timezone <名称> { add | minus } <hh:mm:ss>"),
      W: F(r"^clock timezone\s+", "clock timezone <名称> { add | minus } <hh:mm:ss>"),
      C: F(r"^clock timezone\s+", "clock timezone <名称> <小时> [分钟]",
           "思科的西区用负数，例如美国东部为 clock timezone EST -5。"),
      R: F(r"^clock timezone\s+", "clock timezone <名称> <小时> [分钟]"),
  }),
```

**编译与匹配**（`_compile()`）：

```python
for doc in CATALOG:
    for vk in doc.forms:
        for form in doc.forms_of(vk):
            _COMPILED[vk].append((re.compile(form.pattern), doc))
for vk, items in _COMPILED.items():
    items.sort(key=lambda pair: -len(pair[0].pattern))   # 更具体的模式先试
```

按**模式长度倒序**排序很关键：`interface Vlan-interface 10` 这种更具体的写法
必须先于 `interface GigabitEthernet1/0/1` 命中，否则会被笼统模式抢走。

**四个对外渲染函数** —— 这就是「设备命令详解」页的三种视图：

| 函数 | 产出 | 用在哪 |
|---|---|---|
| `render_explanation(厂商, result)` | 针对**本次生成的命令**逐条详解，按首次出现去重 | 详解页「本次生成的命令详解」 |
| `render_manual(厂商)` | 该厂商全部 132 条概念，按分组排列 | 详解页「该厂商命令手册」 |
| `render_compare()` | 只列两家以上都有的概念，四家写法对照 | 详解页「四厂商命令对照」 |
| `coverage(厂商, result)` | `(已覆盖行数, 总行数, 未识别行列表)` | 状态栏 + 自检 |

**`render_compare()` 是「跨厂商对照」的落点**：因为它按「概念」组织，
天然就能把同一件事的四家写法并排显示 —— 这是按「厂商」组织的命令库做不到的。

---

### 4.8 `scg/verify.py` —— 核验记录（157 行）

纯数据模块 + 一个渲染函数。三块内容：

| 常量 | 含义 |
|---|---|
| `CHECKED` | 对每个厂商**逐条核对过哪些命令**（4 条，每条一大串） |
| `FIXED` | 本轮**实际查出来并修掉的错误**（7 条，含「原因」和「改动」） |
| `USER_MUST_CHECK` | **工具保证不了、必须用户自己在设备上确认的**（10 条） |

`FIXED` 里最典型的一条：

> **基础 / 标准 ACL 误带协议关键字** —— 修正前生成 `rule permit ip source ...`，
> 设备下发时会直接报语法错误。修正后只保留 `source` 字段。

这个模块是**对用户诚实**的落点：不写「命令保证正确」这种空话，
而是把「我做了什么、改了什么、还有什么没把握」摆出来。

---

### 4.9 `scg/ui/dpi.py` —— 高 DPI 适配（46 行）

不处理 DPI 感知的 Tk 程序在 125% / 150% 缩放下会被系统位图拉伸，界面发虚。

```python
def enable_awareness():          # 必须在创建 Tk 窗口之前调用
    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Win8.1+
    # 失败则退回 SetProcessDPIAware()（老系统）

def scale_for(root) -> float:    # 返回 96 DPI = 1.0 的缩放系数
    dpi = float(root.winfo_fpixels("1i"))
    root.tk.call("tk", "scaling", dpi / 72.0)   # Tk 字号跟着调
    return max(1.0, min(3.0, dpi / 96.0))
```

产出 `self.scale`，交给 `theme.apply()`；此后**所有间距都走 `theme.px(值)`**，
按缩放系数换算。这是四家命令以外，第二个「一次写好、到处生效」的地方。

---

### 4.10 `scg/ui/theme.py` —— 双主题调色板（305 行）

两套完整调色板 + 一个 `apply()`：

```python
_LIGHT = {"BG": "#EDEFF3", "PANEL": "#FFFFFF", "FG": "#1F2328",
          "ACCENT": "#2563EB", "GEN": "#16A34A", ...}   # 默认
_DARK  = {"BG": "#1B1B1F", "PANEL": "#232329", "FG": "#E4E4E7", ...}

def _install(palette, dark):
    globals().update(palette)      # 把调色板注入成模块级常量
    IS_DARK = dark

_install(_LIGHT, False)            # 导入时先装一套，保证常量永远有效

def apply(root, scale=1.0, dark=False) -> ttk.Style:
    SCALE = scale
    _install(_DARK if dark else _LIGHT, dark)
    style = ttk.Style(root); style.theme_use("clam")
    ... 配置 TFrame / TLabel / TButton / Gen.TButton / Card.* / Page.TNotebook ...
    return style

def px(value) -> int:              # 96 DPI 设计值 → 当前屏幕像素
    return max(1, int(round(value * SCALE)))
```

**两个技术细节**：

1. **`from __future__ import annotations` 是必须的** —— `BG` / `FG` 这些名字是
   `_install()` 运行时注入的，静态分析看不到，不加这行类型注解会报 `NameError`。
2. **字体在模块级定义**（`FONT_UI` / `FONT_MONO`），界面各处统一引用，
   避免「这个标签用 9pt 那个用 10pt」的漂移。

---

### 4.11 `scg/ui/window.py` —— 主窗口（1087 行）

界面层唯一的实体。结构对齐参考应用的「一键生成配置命令」模块：

```
顶栏  ── Yang-SwitchKit / 浅色·深色 / 关于
────────────────────────────────────────────────────────
页面标签 ── 一键生成配置命令 | 设备命令详解 | 核验说明 | 使用说明
┌────────────────────┬───────────────────────────────────┐
│ ▌一键配置脚本       │ 设备厂商：○思科 ○华为 ○H3C ○锐捷   │
│ ┌────────────────┐ │            [生成全部场景]          │
│ │ 只读脚本输出区  │ │───────────────────────────────────│
│ │ 语法高亮        │ │ 01 VLAN 创建与端口划分 [生成脚本]  │
│ │                │ │ 02 链路聚合（LACP）   [生成脚本]  │
│ └────────────────┘ │ ...（22 张卡片，一页到底）         │
│ [复制][导出][清空] │                                   │
│           □保留注释 │                                   │
└────────────────────┴───────────────────────────────────┘
状态栏 ── 版本 | 当前厂商 | 已生成 n/22 | 详解覆盖 x/y 行 | 主题
```

**核心状态**（跨重建保留）：

| 变量 | 作用 |
|---|---|
| `self.store[(厂商, 场景)]` | 每个厂商、每个场景独立存参数，切厂商不丢值 |
| `self.results[场景key]` | 当前厂商下各场景的 `GenResult` |
| `self.order` | 已生成过的场景顺序 |
| `self.errors[场景key]` | 生成失败的场景与原因 |
| `self.widgets[场景key][参数key]` | 控件索引，`_collect()` 靠它读值 |
| `self.show_comment` | 「保留注释」勾选框 |

**几个关键方法**：

| 方法 | 职责 |
|---|---|
| `_build_all()` | 一次性重建整个窗口（主题切换也走这里） |
| `_build_card()` | 画一张场景卡片：序号 + 名称 + 分组简介 + 绿色「生成脚本」 |
| `_build_param()` | 按 `Param.kind` 决定画输入框 / 下拉框 / 勾选框 / 多行框 |
| `_collect(scenario)` | 把控件值读回 `{"参数名": "值"}` |
| `_restore_fields()` | 反向：把存过的值填回控件 |
| `_generate(key)` | 调 `vendors.generate()`，成功存 `results`，失败存 `errors` |
| `_gen_all()` | 22 个场景一次跑完 |
| `_script_text()` | 唯一的文本产出点（脚本区 / 复制 / 导出共用） |
| `_plain_text(keys)` | 「取消注释」模式：丢掉所有 `#` / `!` 开头行 |
| `_refresh_output()` | 把文本写进只读输出框，逐行上色 |
| `_refresh_doc()` | 渲染详解页当前视图 |
| `_toggle_theme()` | 换主题 —— **必须整页重建**，见下文 |

---

### 4.12 `tools/` —— 五个自检脚本

| 脚本 | 怎么用 | 保证什么 |
|---|---|---|
| `smoke_test.py` | `python tools/smoke_test.py` | 4 × 22 = 88 组全用默认参数生成一遍，任何异常即失败 |
| `verify_exe.py` | `python tools/verify_exe.py <exe>` | 打包产物能起来、窗口标题对、尺寸合理、**不留孤儿进程** |
| `wincap.py` | 被其它脚本 import | 用 `PrintWindow` 只抓窗口自身画面 |
| `shot.py` | 开发自检 | 截 4 张图看视觉效果 |
| `make_icon.py` | 打包时自动调用 | 用代码画 ico，不依赖外部图片资源 |

`wincap.py` 值得单独说：**直接用截图工具抓屏幕，会把当时压在窗口上面的任何东西
一起拍下来**（通知、聊天窗口）。所以改成让窗口把自己重绘到内存 DC 再取位图。
里面还踩过两个坑：一是 `PrintWindow` 画的是整个窗口含标题栏，但位图按客户区建，
导致底部状态栏被裁掉 —— 现在按 `GetWindowRect` 建图、用 `ClientToScreen` 算偏移再裁回客户区；
二是 64 位下 `GetParent` / `GetDC` 这些 API 必须显式声明 `argtypes` / `restype`，
否则句柄会被当 int 截断。

---

### 4.13 `build.py` —— 打包（77 行）

```
1. 检查 PyInstaller（缺了就给带阿里云镜像的安装命令）
2. 调 tools/make_icon.py 生成 assets/app.ico
3. 清掉旧的 build/ 与 dist/
4. PyInstaller --onefile --windowed --name YangSwitchKit
5. 打印产物路径与体积
```

`--exclude-module` 排掉了 numpy / pandas / matplotlib / PIL / pytest，把体积压到 11 MB。

**必须用带 PyInstaller 的解释器跑**（本机是 `envs/switchgen`，系统 Python 里没有）。

---

## 五、模块之间的联动

前面是「每个模块是什么」，这一节是「它们怎么串起来」。

### 5.1 启动链路

```
main.py
  └─ scg.ui.window.run()
       └─ App.__init__()
            ├─ dpi.enable_awareness()              ① 先声明 DPI 感知（必须在建窗口前）
            ├─ tk.Tk.__init__()                    ② 建窗口
            ├─ self.scale = dpi.scale_for(self)    ③ 拿到缩放系数，调 Tk 字号
            ├─ theme.apply(self, scale, dark)      ④ 装调色板 + ttk 样式
            ├─ 建状态字典（store / results / order / errors / widgets）
            └─ _build_all()
                 ├─ _build_banner()                顶栏
                 ├─ _build_pages()                 4 个页面
                 │    ├─ _build_main_page()
                 │    │    ├─ _build_script_pane()   左：脚本区
                 │    │    └─ _build_param_pane()    右：厂商条 + 22 张卡片
                 │    │         └─ 遍历 registry.SCENARIOS → _build_card() × 22
                 │    │              └─ 遍历 scenario.params → _build_param()
                 │    ├─ _build_doc_page()           详解页
                 │    └─ _build_text_page() × 2      核验页 / 使用说明页
                 ├─ _build_status()
                 ├─ _restore_fields()              从 store 回填
                 ├─ _sync_gen_buttons()            按厂商禁用不支持的场景
                 ├─ _refresh_output()              渲染脚本区
                 └─ _refresh_doc()                 渲染详解页
```

注意 **`_build_all()` 是幂等的**：主题切换、重建界面都调它。
它不碰 `store` / `results` / `order` 这些跨重建状态，所以重建后数据还在。

---

### 5.2 「点生成脚本」这一下，串了 5 个模块

```
用户点第 03 张卡片的绿色按钮
  │
  ▼  window.py::_gen_one("stp")
     │
     ├─▶ _generate("stp")
     │    │
     │    ├─▶ registry.get("stp")                    ← registry 提供 Scenario 对象
     │    ├─▶ _collect(scenario)                     ← 从 Tk 控件读值
     │    ├─▶ store[(厂商,"stp")] = params           ← 存起来，切厂商不丢
     │    ├─▶ vendors.supports(厂商, "stp")          ← vendors 层面判断支持性
     │    ├─▶ vendors.generate(厂商, "stp", params, scenario)
     │    │    ├─▶ vendors/__init__.generate()       路由到对应模块
     │    │    ├─▶ h3c.py::_HANDLERS["stp"]          找到处理函数
     │    │    ├─▶ h3c.py::_stp(p, scenario)
     │    │    │    ├─▶ ports.expand(p["edge_ports"], "h3c")    ← ports 展开端口
     │    │    │    ├─▶ B.parse_pairs(p["instances"])           ← base 解析实例映射
     │    │    │    ├─▶ B.Builder(...).block(...).note(...)     ← base 累积结果
     │    │    │    └─▶ b.done() → GenResult
     │    │    └─▶ 追加 scenario.notes
     │    │
     │    ├─ 成功 → results["stp"] = result
     │    └─ 失败（ValueError）→ errors["stp"] = 原因，界面红字显示
     │
     ├─▶ _update_card_state("stp")     卡片状态行：✓ 已生成，本段 N 行命令
     ├─▶ _refresh_output()             左侧脚本区重绘
     │    ├─ _active_keys()            问 registry：哪些场景生成过
     │    ├─ _script_text() 或 _plain_text()   产出纯文本
     │    └─ _line_tag()               逐行决定颜色（注释/命令/风险提示）
     ├─▶ _refresh_doc()                详解页重绘
     │    ├─ _merged()                 把多个 GenResult 拼成一个
     │    └─ explain.render_explanation(厂商, merged)   ← explain 干活
     └─▶ _flash("已生成【...】的命令")  状态栏临时提示
```

**这条链上最有意思的一点**：`_merged()` 会把脚本区里**已有的所有场景**合成一个
`GenResult` 再交给详解页。所以「命令详解」讲的是**你手上这份脚本**，
而不是「当前选中的那个场景」—— 跟左侧脚本区严格一一对应。

---

### 5.3 详解是怎么算出来的（registry ←→ vendors ←→ explain 三方对齐）

三个模块靠**同一个 `scenario.key`** 对齐：

```
registry.SCENARIOS[i].key = "port_security"
        │                            │
        │ 声明参数                    │ 声明处理函数
        ▼                            ▼
vendors/*.py  _HANDLERS["port_security"] = _port_security
        │
        │ 这个函数吐出的命令行
        ▼
explain.py  CATALOG 里的正则去认这些行
```

三方对齐的**验证手段**就是 `explain.coverage()`：

```python
hit, total, miss = explain.coverage(vendor_key, result)
```

它把生成结果里每一行拿去匹配概念，返回覆盖率和**未识别的行**。
自检脚本对 4 厂商全场景跑一遍，要求 **100%**：

```
h3c    366/366      huawei 335/335
cisco  329/329      ruijie 336/336
合计   1366/1366 = 100.00%
```

**这个数字是个安全网**：加新场景时如果忘了在 `explain.py` 补详解，
覆盖率会立刻掉下来，而不是等用户来发现「这条命令没解释」。

---

### 5.4 换厂商的链路（界面 ↔ 存储 ↔ 命令库）

```
用户点另一个厂商单选按钮
  │
  ▼  window.py::_on_vendor_change()
     ├─ _save_fields()            把当前厂商所有卡片的值存进 store[旧厂商, ...]
     ├─ self.vendor_key = 新厂商
     ├─ 更新接口命名提示（来自 Vendor.port_hint）
     ├─ _restore_fields()         从 store[新厂商, ...] 回填控件
     ├─ results.clear() / errors.clear()
     ├─ 对 order 里「已生成过」的场景逐个 _generate()
     │     └─ 同样调 vendors.generate()，只是换了厂商 key
     ├─ _refresh_output()
     └─ _refresh_doc()
```

**设计要点：参数表不随厂商变**。22 个场景的参数定义在 `registry.py` 里只有一份，
四家共用 —— 因为「VLAN 号」「端口」「聚合组号」这些概念是跨厂商的，
变的只有**命令怎么写**。所以换厂商只需要重算命令，不用重建表单。

**为什么按 `(厂商, 场景)` 分开存值**：端口写法四家不一样
（华为从 `0/0/1` 开始，H3C 从 `1/0/1` 开始），
给 H3C 填过的端口直接套到华为上是错的。分开存虽然占点内存，但语义上是对的。

---

### 5.5 换主题为什么要整页重建

Tk 的颜色是**控件创建时读进内存的**，改完调色板旧控件不会自动刷新：

```python
def _toggle_theme(self):
    self.dark = not self.dark
    theme.apply(self, self.scale, self.dark)     # ① 先换模块级常量
    self._build_all()                            # ② 整页销毁重建
```

`_build_all()` 的第一件事就是 `for child in self.root.children: child.destroy()`，
然后重新走一遍 5.1 的建界面链路。

**数据为什么不会丢**：`store` / `results` / `order` / `errors` 是 `App` 的普通字典，
不属于任何控件，重建后照样在；`_restore_fields()` 会把值填回新控件。
`show_comment` / `doc_mode` / `vendor_var` 这几个 `tk.Variable` 在 `__init__` 里创建，
也没跟着控件销毁。

---

### 5.6 「新增一个场景」要动几处 —— 横向联动清单

这是最能体现模块联动的操作，**只需改三个文件、登记五处**：

| # | 文件 | 改什么 | 漏了会怎样 |
|---|---|---|---|
| 1 | `registry.py` | 加一个 `Scenario(key="xxx", params=(...))` | 界面没这张卡片 |
| 2 | `vendors/h3c.py` | 写 `_xxx(p, scenario)`，注册进 `_HANDLERS` | 该厂商报「暂不支持场景」 |
| 3 | `vendors/huawei.py` | 同上 | 同上 |
| 4 | `vendors/cisco.py` | 同上 | 同上 |
| 5 | `vendors/ruijie.py` | 同上 | 同上 |
| 6 | `explain.py` | 在 `CATALOG` 补新命令的概念条目 | **覆盖率掉下来**，命令没解释 |

**界面完全不用改** —— 表单是 `_build_card()` 遍历 `scenario.params` 自动生成的。
改完跑一遍：

```bash
python tools/smoke_test.py      # 组合数会从 88 变成 92，应全部通过
```

再看覆盖率是不是还是 100%。这两条过了就算接好了。

---

### 5.7 打包链路

```
build.py
  ├─ tools/make_icon.py     → assets/app.ico
  ├─ PyInstaller
  │    └─ 从 main.py 出发静态分析 import 图
  │         main.py → scg.ui.window
  │                 → scg.registry / scg.explain / scg.verify
  │                 → scg.vendors（四个模块必须显式 import！）
  └─ dist/YangSwitchKit.exe
```

**`vendors/__init__.py` 里的显式 import 是必须的**：

```python
# 必须显式导入：PyInstaller 靠静态分析收集模块，动态 import 的模块不会被打进 exe
from . import cisco, h3c, huawei, ruijie
```

原来写成 `importlib.import_module()` 动态加载时，打包出来的 exe 一运行就报
「找不到厂商模块」—— 静态分析看不到动态导入的字符串。

---

## 六、关键设计决策

| 决策 | 为什么 |
|---|---|
| **命令分厂商、知识按概念** | 详解要能解释命令，对照表要能横向比。按厂商组织就只能横向比 |
| **`GenResult` 单一结果载体** | 脚本区 / 详解 / 导出 / 覆盖率全从它派生，不可能不一致 |
| **参数表跨厂商只有一份** | 概念是跨厂商的，变的只有命令写法；换厂商不用重建表单 |
| **按 (厂商, 场景) 分开存值** | 端口命名习惯四家不同，混用会生成错命令 |
| **查不出把握就不生成** | 宁可给注释 + 提示，也不生成一条会在设备上报错的命令 |
| **覆盖率必须 100%** | 覆盖率的下降是「忘了写详解」的自动报警器 |
| **主题切换整页重建** | Tk 颜色是创建时读的，这是最省心的正确做法 |
| **不 import UI 的命令库** | `ui/` 删掉命令库照样能跑，自检脚本才能不依赖图形环境 |
| **截图只抓窗口自身** | 用户桌面上可能有私人窗口，抓屏会误拍 |

---

## 七、已知边界

1. **保证语法结构，不保证参数取值** —— 网段、VLAN、端口要用户自己定，工具不校验现网冲突。
2. **版本差异必须人工确认** —— 凡是标注了「部分版本」的命令，下发前要用 `?` 补全确认。
3. **堆叠 / IRF / VSU 差异最大** —— 给的是骨架，必须对照本机型号手册。
4. **思科没有环路检测** —— 工具给的是 PortFast + BPDU 保护 + errdisable 的替代方案。
5. **静态 IP+MAC 绑定不生成** —— 四家参数模型都不一样，风险大于收益，只给命令模板注释。
6. **明文密码** —— 生成结果里的密码是明文，仅用于开局，交付前必须统一更换。
7. **不连设备、不下发** —— 只产出文本，最终执行由人决定。

---

## 八、开发者快速上手

```bash
# 跑命令全组合冒烟测试（不需要图形环境）
python tools/smoke_test.py

# 看某个场景的具体输出
python tools/smoke_test.py --show h3c/stacking

# 检查详解覆盖率
python -c "from scg import registry, explain; ..."   # 见 README「二次开发」

# 打包
python build.py
```

**两个必须记住的坑**（踩过，写在代码注释里了）：

1. **`explain.match()` 匹配前会 `strip()` 行首空格** —— 所以正则模式**绝不能带前导空格**，
   带了就永远匹配不上，表现为覆盖率莫名掉一大截。
2. **拆参数要用 `base.split_ifaces()`，不能用空白拆** ——
   `Vlan-interface 10`、`GigabitEthernet 0/0/1` 这类接口名自带空格，用空白拆会劈成两半。

改完代码记得跑 `tools/smoke_test.py`，它会告诉你 88 组里有没有哪组挂了。
