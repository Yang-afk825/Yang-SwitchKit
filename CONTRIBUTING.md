# 参与贡献

这个项目的全部价值在「命令是对的」。所以贡献方式里，纠错比加功能更重要。

---

## 一、最需要你做的事：报一个错

如果你在设备上发现生成的命令跑不通，这条反馈比提十个新功能更有用。

提 Issue 时请带上这几样，缺一样都很难定位：

1. **哪个厂商、什么型号、什么软件版本**
   例如「华为 S5735，VRP V200R019」。「华为交换机」这种粒度不够——
   同一个功能在不同版本上的语法可能完全不同。
2. **哪个场景、填了什么参数**
3. **生成的哪一条命令出错**
4. **设备上的原始回显**
   `^` 和 `Error:` / `Unrecognized command` 那几行原样贴出来，
   它会精确指向出错的字符位置。
5. **正确的命令是什么**
   最好附上 `?` 补全看到的候选，或者官方手册的章节。

这几样齐了，通常当天就能改。

> 只写「XX 命令不对」而没有回显的 Issue，没法处理，只能关掉。

---

## 二、改一条命令

命令全部在 `scg/vendors/` 下，四个厂商一个文件，改哪家动哪家，互不影响：

```
scg/vendors/
├── h3c.py       Comware
├── huawei.py    VRP
├── cisco.py     IOS
└── ruijie.py    RGOS
```

每个文件底部有一张 `_HANDLERS` 表，把场景 key 映射到生成函数：

```python
_HANDLERS: dict[str, object] = {
    "vlan_port": _vlan_port,
    "lag": _lag,
    ...
}

def _lag(p, scenario) -> GenResult:
    ...
```

`p` 是参数字典（字符串值，直接取自界面），`scenario` 是 registry 里那个场景对象。
返回值用 `base.py` 里的 `GenResult` / `Block` 构造，不要自己拼字符串返回。

改完必须跑三个检查，三个都过才算完：

```bash
python tools/smoke_test.py     # 116 组能不能都生成出来
python tools/golden_test.py    # 输出有没有被意外改动（会打印 diff）
python build.py                # 能不能打包成 exe
```

---

## 三、golden 测试怎么用

`tools/golden_test.py` 是防止「改 A 弄坏 B」的。它把 4 厂商 × 29 场景 × 2 变体的
输出存成快照（`tests/golden/`），改完代码逐行比对。

正确流程：

```bash
python tools/golden_test.py            # 1. 看 diff，确认改动范围符合预期
python tools/golden_test.py --update   # 2. 确认无误后重建快照
```

然后把**快照文件和代码一起提交**。快照的 diff 本身就是给 reviewer 看的变更说明——
他只读 diff 就知道你改了哪些命令，不用逐行读代码。

有两点要留意：

- 如果你只改了华为的镜像命令，diff 里却出现了锐捷的输出变化，说明改错了地方。
- 快照里出现 `<<<生成失败：...>>>` 的内容不是坏事，见下一条。

### 该被拦下的参数，要被一直拦住

有些参数组合本来就应该报错，例如给 H3C 选 HSRP、巡检类一项都不勾。
`golden_test.py` 顶部的 `EXPECTED_FAIL` 记录了这些组合，快照里存的是拦截文案。

**如果你解除了某个限制（比如某型号新版本其实支持了），记得同步删除
`EXPECTED_FAIL` 里对应的那一行**，否则测试会一直报「本该被拦下却生成了命令」。

反过来，如果你无意中把某个校验删掉了，测试会立刻报警——这是它故意的。

---

## 四、加一个新场景

一个场景要动四个地方，漏掉任何一处都不算完成。

**第 1 步：在 `scg/registry.py` 声明场景**

```python
Scenario(
    key="my_scenario",            # 全局唯一，后面三处都用这个 key
    name="界面上的名字",
    group="二层",                  # 二层 / 三层 / 高可用 / 安全 / 运维
    summary="一句话说明这个场景解决什么问题。",
    versions="Comware V5/V7 · VRP V5/V8 · IOS 12.x/15.x · RGOS 10.x/11.x",
    params=(
        Param("ports", "端口", Kind.TEXT, "1/0/1-8", help=PORT_HINT),
        Param("mode", "模式", Kind.CHOICE, "a",
              choices=(("a", "说明 A"), ("b", "说明 B"))),
    ),
    notes=("使用这个功能时需要注意的事，会以 [提示] 附在输出末尾。",),
    reboot=False,                 # 这个场景会不会导致设备重启或断业务
)
```

界面表单是从 `params` **自动生成**的，不需要写任何 UI 代码。
参数类型有 `TEXT / NUM / CHOICE / BOOL / AREA` 五种，布尔值用字符串 `"1"` / `"0"`。

**第 2 步：在四个厂商文件里各写一个生成函数，并注册进 `_HANDLERS`**

四家都要写。如果某家确实不支持这个功能（例如思科没有发探测帧的环路检测），
就**不要**在它的 `_HANDLERS` 里注册，程序会把该场景标记为不支持，
而不是生成一段错的命令。宁可缺，不可错。

**第 3 步：在 `scg/explain.py` 的 `CATALOG` 里补命令详解**

```python
D("命令的中文名", "二层",
  "这条命令干什么、什么时候用，用完整的句子讲清楚。",
  forms={
      "h3c":    F(r"^speed\s+(\S+)", "speed <auto|10|100|1000>",
                  "可选说明：容易踩的坑、版本差异。"),
      "huawei": F(...),
      "cisco":  F(...),
      "ruijie": F(...),
  },
  caution="配错的后果，没有就留空。"),
```

`F()` 的第一个参数是正则，用来匹配生成的命令行；第二个是界面上展示的语法原型。
正则写不对，这条命令就会算作「未覆盖」。

**这一步不能省。** `golden_test.py` 会统计详解覆盖率，只要有一行命令匹配不上
就在结尾报错。README 承诺的是 100%，掉下来测试就红。

**第 4 步：跑测试、更新快照**

```bash
python tools/smoke_test.py
python tools/golden_test.py            # 会提示缺少新场景的快照
python tools/golden_test.py --update
```

覆盖率、预期失败、快照三项全绿再提交。

---

## 五、提交前检查清单

- [ ] `python tools/smoke_test.py` 全部通过
- [ ] `python tools/golden_test.py` 结论是「全部一致」（或 diff 符合预期且已 `--update`）
- [ ] 详解覆盖率仍是 100%，新命令都在 `explain.py` 里有条目
- [ ] 新增场景在四个厂商文件里都注册了（或明确标注某家不支持）
- [ ] 动过 `scg/project.py` 就追加跑 `python tools/project_test.py`
- [ ] 动过 `scg/compose.py`、或改了任何厂商的 `_ENTER` / `_SAVE` 常量，
      追加跑 `python tools/compose_test.py`
- [ ] 动过 `scg/ui/` 就追加跑 `python tools/ui_smoke.py`
- [ ] 改过思科 / 锐捷的任何块，追加跑 `python tools/view_audit.py`，
      确认没有特权命令落在配置视图里
- [ ] 注释和说明用中文，语气跟现有代码保持一致
- [ ] `python build.py` 能打出 exe

---

## 六、命令正确性的举证要求

这是本项目唯一一条硬性门槛。

凡是改动或新增命令的 PR，**描述里必须给出这条命令的出处**，三选一：

1. 官方命令手册的章节名或链接
2. 设备上 `?` 补全后的回显截图（型号版本要能看清）
3. 模拟器（eNSP / HCL / GNS3）实跑的截图，包含命令和回显

没有出处的命令改动不予合并，即使它「看起来对」。

原因很直接：这个工具的卖点是「比问 AI 靠谱」。靠猜出来的命令写进库里，
它就变成另一个会自信地给出错误答案的东西了。

另外，这些跨厂商陷阱请特别留意，历史上的错误大多出在这里：

- ACL 编号体系（思科/锐捷 100-199 是扩展 ACL，H3C/华为 2000-2999 是基础 ACL）
- 基础 ACL 里不能出现协议关键字、目的地址、目的端口
- 时区格式（H3C/华为要 `add 08:00:00`，思科/锐捷要 `8 0`，都不吃 `UTC+08:00`）
- 接口名本身带空格（`Vlan-interface 10`），按空白拆分参数会被劈成两条命令
- 默认 VLAN 的四种叫法（H3C pvid / 华为 default vlan / 思科 native vlan）

---

## 七、代码约定

- **只用标准库。** 不引入 requests / rich / PyQt 之类的第三方依赖。
  这个工具要能在内网、在断网机器上跑，不能有任何 pip 安装步骤。
- **UI 用 Tkinter。** 界面代码集中在 `scg/ui/`，命令库不 import 任何 UI 模块。
- **命令库不依赖界面。** `scg/vendors/` 和 `scg/registry.py` 要能脱离 Tkinter 独立运行，
  测试脚本就是这么用的。
- **数据层也不依赖界面。** `scg/project.py` 只管方案文件的读写，不许 import tkinter，
  否则 `tools/project_test.py` 就没法在无图形环境的机器上跑了。
- **中文注释。** 变量名用英文，注释和用户可见的文案用中文。
- 新增文件请同步更新 README 的目录结构那一节。

---

## 八、许可

提交的代码默认以 [CC BY-NC-SA 4.0](LICENSE) 授权发布，与项目保持一致。

请不要在贡献里加入来自其他项目、且许可证不兼容的代码。
