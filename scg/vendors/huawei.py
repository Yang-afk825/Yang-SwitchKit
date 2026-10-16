"""华为 VRP（V5 / V8）命令生成。

注意：华为接口编号从 0 开始（GigabitEthernet0/0/1），与 H3C 的 1/0/1 不同。
"""
from __future__ import annotations

from .. import ports
from ..model import GenResult, Vendor
from . import base as B

VENDOR = Vendor(
    key="huawei", name="华为 Huawei", platform="VRP V5 / V8", accent="#EF4444",
    port_hint="接口编号通常形如 GigabitEthernet0/0/1（从 0 开始，与 H3C 不同）；"
              "万兆为 XGigabitEthernet0/0/49；三层接口为 Vlanif 10。",
)

_ENTER = "system-view"
_SAVE = ("return", "save")


def _if_blocks(port_list, body, indent=" ") -> list[str]:
    if not port_list:
        return []
    ok, first, last = ports.sequential(port_list)
    if ok:
        lines = [f"interface range {first} to {last}"]
        lines += [indent + ln for ln in body]
        lines.append(" quit")
        return lines
    out: list[str] = []
    for name in port_list:
        out.append(f"interface {name}")
        out += [indent + ln for ln in body]
        out.append(" quit")
    return out


# --------------------------------------------------------------------------
# 二层
# --------------------------------------------------------------------------

def _vlan_port(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(p["vlans"])
    names = B.parse_vlan_names(p.get("vlan_names", ""))
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)

    lines = [_ENTER]
    for v in vlans:
        lines.append(f"vlan {v}")
        if names.get(v):
            lines.append(f" description {names[v]}")
        lines.append(" quit")
    b.block("创建 VLAN", lines,
            comment="华为的 VLAN 描述字段是 description，H3C 与思科用 name，注意区别。")

    if port_list:
        if p["port_type"] == "trunk":
            allowed = (p.get("allowed", "") or "").strip() or "all"
            if allowed.lower() == "all":
                permit = "port trunk allow-pass vlan all"
                b.warn("Trunk 放通全部 VLAN 会扩大广播域，建议改成明确的 VLAN 列表。")
            else:
                permit = f"port trunk allow-pass vlan {B.compact_vlans(B.parse_vlans(allowed), ' ', ' to ')}"
            body = ["port link-type trunk", permit]
            pvid = str(p.get("pvid", "")).strip()
            if pvid and pvid != "1":
                body.append(f"port trunk pvid vlan {pvid}")
            b.block("配置 Trunk 端口", _if_blocks(port_list, body))
        else:
            if len(vlans) == 1:
                mapping = {n: vlans[0] for n in port_list}
            elif len(vlans) == len(port_list):
                mapping = dict(zip(port_list, vlans))
                b.note("VLAN 数与端口数相同，已按顺序一一对应划分。")
            else:
                mapping = {n: vlans[0] for n in port_list}
                b.warn(f"VLAN 有 {len(vlans)} 个、端口有 {len(port_list)} 个，无法一一对应，"
                       f"已统一划入 VLAN {vlans[0]}。")
            by_vlan: dict[int, list[str]] = {}
            for name, vid in mapping.items():
                by_vlan.setdefault(vid, []).append(name)
            for vid, group in by_vlan.items():
                b.block(f"Access 端口划入 VLAN {vid}",
                        _if_blocks(group, ["port link-type access", f"port default vlan {vid}"]),
                        comment="华为的 Access 端口用 port default vlan，H3C 是 port access vlan。")

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


def _lag(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group"), "聚合组号")
    members = ports.expand(p.get("ports", ""), VENDOR.key)
    link_type = p.get("link_type", "trunk")

    if link_type == "l3":
        agg_if = f"Eth-Trunk {group}"
        body: list[str] = []
        b.note("Eth-Trunk 配三层地址前，需先执行 undo portswitch 转为三层接口。")
    else:
        agg_if = f"Eth-Trunk {group}"
        body = []
        if link_type == "trunk":
            allowed = (p.get("allowed", "") or "").strip() or "all"
            body.append("port link-type trunk")
            if allowed.lower() == "all":
                body.append("port trunk allow-pass vlan all")
            else:
                body.append(f"port trunk allow-pass vlan {B.compact_vlans(B.parse_vlans(allowed), ' ', ' to ')}")
            pvid = str(p.get("pvid", "")).strip()
            if pvid and pvid != "1":
                body.append(f"port trunk pvid vlan {pvid}")
        else:
            body.append("port link-type access")

    if p["mode"] == "lacp":
        body.append("mode lacp-static")
    else:
        body.append("mode manual load-balance")

    lines = [_ENTER, f"interface {agg_if}"] + [" " + ln for ln in body] + [" quit"]
    b.block("创建聚合接口", lines,
            comment="华为动态聚合为 mode lacp-static，静态聚合为 mode manual load-balance。")

    member_lines = [_ENTER]
    for name in members:
        member_lines += [f"interface {name}", f" eth-trunk {group}", " quit"]
    b.block("成员端口加入聚合组", member_lines)

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


def _stp(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    mode = p["mode"]
    mode_name = {"mstp": "mstp", "rstp": "rstp", "stp": "stp"}[mode]

    b.block("设置生成树模式", [_ENTER, "stp enable", f"stp mode {mode_name}"])

    if mode == "mstp":
        instances = B.parse_pairs(p.get("instances", ""))
        conf = [_ENTER, "stp region-configuration",
                f" region-name {B.ensure(p.get('region'), 'MST 域名')}",
                f" revision-level {p.get('revision') or 0}"]
        for inst, vlan_spec in instances.items():
            vlans = B.parse_vlans(vlan_spec)
            conf.append(f" instance {inst} vlan {B.compact_vlans(vlans, ' ', ' to ')}")
        conf += [" active region-configuration", " quit"]
        b.block("MSTP 域配置", conf,
                comment="同一域内所有设备的域名、修订级别、实例映射必须完全一致。")
        if not instances:
            b.warn("未填写实例与 VLAN 映射，所有 VLAN 默认映射到实例 0。")

    role = p.get("role", "none")
    if role in ("primary", "secondary"):
        inst_list = list(B.parse_pairs(p.get("instances", "")).keys()) or [0]
        lines = [_ENTER]
        if mode == "mstp":
            for inst in inst_list:
                lines.append(f"stp instance {inst} root {role}")
        else:
            lines = [_ENTER, f"stp root {role}"]
        b.block("指定根桥角色", lines)

    edge = ports.expand(p.get("edge_ports", ""), VENDOR.key)
    if edge:
        b.block("接入口设为边缘端口",
                [_ENTER] + _if_blocks(edge, ["stp edged-port enable"]),
                comment="华为是 stp edged-port enable，H3C 是 stp edged-port。")

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


# --------------------------------------------------------------------------
# 三层
# --------------------------------------------------------------------------

def _dhcp(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlan_id = B.ensure(p.get("vlan_id"), "三层接口 VLAN")
    ip = B.ensure(p.get("ip"), "接口 IP")
    mask = int(p.get("mask") or 24)
    iface = f"Vlanif {vlan_id}"

    if p["mode"] == "relay":
        servers = B.parse_ip_list(p.get("relay", ""))
        if not servers:
            raise ValueError("中继模式下必须填写中继目标服务器地址。")
        lines = [_ENTER, "dhcp enable",
                 f"interface {iface}",
                 f" ip address {ip} {mask}",
                 " dhcp select relay"]
        lines += [f" dhcp relay server-ip {s}" for s in servers]
        lines.append(" quit")
        b.block("配置 DHCP 中继", lines,
                comment="华为中继用 dhcp relay server-ip，H3C 用 dhcp relay server-address。")
        b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
        return b.done()

    pool = B.ensure(p.get("pool"), "地址池名称")
    network = B.ensure(p.get("network"), "地址池网段")
    lines = [_ENTER, "dhcp enable",
             f"ip pool {pool}",
             f" network {network} mask {B.prefix_to_dotted(mask)}"]
    if p.get("gateway"):
        lines.append(f" gateway-list {p['gateway'].strip()}")
    dns = B.parse_ip_list(p.get("dns", ""))
    if dns:
        lines.append(f" dns-list {' '.join(dns)}")
    excludes = B.parse_ip_list(p.get("exclude", ""))
    if excludes:
        lines.append(f" excluded-ip-address {excludes[0]} {excludes[-1]}"
                     if len(excludes) > 1 else f" excluded-ip-address {excludes[0]}")
    lines.append(" quit")
    lines += [f"interface {iface}",
              f" ip address {ip} {mask}",
              " dhcp select global",
              " quit"]
    b.block("配置 DHCP 服务器", lines,
            comment="华为的三层接口名是 Vlanif，H3C 是 Vlan-interface，思科是 Vlan。")
    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


def _static_route(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    raw = str(p.get("pref") or "").strip()
    base = int(raw) if raw.isdigit() else 0
    routes = B.parse_routes(p.get("routes", ""), base, p.get("desc", ""))
    if not routes:
        raise ValueError("请至少填写一条路由。")

    lines = [_ENTER]
    for r in routes:
        cmd = f"ip route-static {r.net} {r.prefix} {r.nexthop}"
        if r.pref:
            cmd += f" preference {r.pref}"
        if p.get("desc"):
            cmd += f" description {p['desc'].strip()}"
        lines.append(cmd)
    b.block("配置静态路由", lines,
            comment="华为静态路由默认优先级 60，数值越大优先级越低。")
    lines = [_ENTER]
    for r in routes:
        lines.append(f"display ip routing-table {r.net}")
    b.block("下发后核对", lines)
    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


# --------------------------------------------------------------------------
# 安全 / 运维
# --------------------------------------------------------------------------

def _acl(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    acl_id = (p.get("acl_id") or "").strip()
    rules = B.parse_acl_rules(p.get("rules", ""))
    if not rules:
        raise ValueError("请至少填写一条 ACL 规则。")
    advanced = B.acl_is_advanced(acl_id, VENDOR.key) if acl_id else True
    if not acl_id:
        acl_id = "3000" if advanced else "2000"
        b.note(f"未填写 ACL 编号，已默认使用 {acl_id}。")

    lines = [_ENTER, f"acl number {acl_id}"]
    if p.get("desc"):
        lines.append(f" description {p['desc'].strip()}")
    for i, rule in enumerate(rules, start=1):
        parts = [f" rule {i * 5} {rule.action}"]
        if advanced:
            # 高级 ACL 才能写协议、目的地址、目的端口
            parts.append(rule.proto)
            if rule.src.lower() != "any":
                net, plen = B.parse_net(rule.src)
                parts.append(f"source {net} {B.prefix_to_wildcard(plen)}")
            if rule.dst.lower() != "any":
                net, plen = B.parse_net(rule.dst)
                parts.append(f"destination {net} {B.prefix_to_wildcard(plen)}")
            if rule.op:
                parts.append(f"destination-port {rule.op} {rule.port}")
        else:
            # 基本 ACL 只能匹配源地址，带协议关键字会被设备拒绝
            if rule.src.lower() == "any":
                parts.append("source any")
            else:
                net, plen = B.parse_net(rule.src)
                parts.append(f"source {net} {B.prefix_to_wildcard(plen)}")
        lines.append(" ".join(parts))
    lines.append(" quit")
    b.block("定义 ACL", lines,
            comment="华为 ACL 规则步长默认 5，编号按 5 递增。")

    if not advanced and any(r.dst.lower() != "any" or r.op for r in rules):
        b.warn("当前是基本 ACL（只能匹配源地址），规则里的目的地址与端口字段已按语法忽略；"
               "需要按五元组过滤请改用 3000-3999 的高级 ACL。")

    if any(r.action == "permit" for r in rules) and not any(r.action == "deny" for r in rules):
        b.warn("规则中没有 deny，建议补一条显式拒绝以避免意外放行。")

    apply_ports = ports.expand(p.get("apply_port", ""), VENDOR.key)
    if apply_ports:
        direction = p.get("direction", "inbound")
        keyword = "inbound" if direction == "inbound" else "outbound"
        b.block("应用到端口",
                [_ENTER] + _if_blocks(apply_ports, [f"traffic-filter {keyword} acl {acl_id}"]),
                comment="华为在接口下用 traffic-filter 调用 ACL。")

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


def _mirror(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group"), "镜像组号")
    monitor = ports.expand(p.get("monitor", ""), VENDOR.key)
    if len(monitor) != 1:
        raise ValueError("监控端口只能填一个。")

    b.block("定义观察端口", [
        _ENTER, f"observe-port {group} interface {monitor[0]}",
    ], comment="华为的监控口叫 observe-port（观察端口），先定义再引用。")

    vlan = (p.get("vlan") or "").strip()
    if vlan:
        b.block("配置 VLAN 镜像", [
            _ENTER, f"vlan {vlan}",
            f" mirroring to observe-port {group} inbound",
            " quit",
        ], comment="主流 V200 版本只支持镜像 VLAN 收到的报文（inbound），不支持 both / outbound；"
                   "CloudEngine 等系列写作 mirroring observe-port <索引> {inbound | outbound | both}。")
        if p.get("direction", "both") != "inbound":
            b.note("已按设备实际能力强制为 inbound；若确需出方向镜像，请改用端口镜像。")
    else:
        source = ports.expand(p.get("source", ""), VENDOR.key)
        if not source:
            raise ValueError("必须指定被镜像端口。")
        b.block("配置端口镜像",
                [_ENTER] + _if_blocks(source, [f"port-mirroring to observe-port {group} {p.get('direction', 'both')}"]),
                comment="同一端口只能镜像到一个观察端口。")

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


def _mgmt(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    sysname = B.ensure(p.get("sysname"), "设备名")
    vlan_id = B.ensure(p.get("mgmt_vlan"), "管理 VLAN")
    mgmt_ip = B.ensure(p.get("mgmt_ip"), "管理地址")
    mask = int(p.get("mask") or 24)
    user = B.ensure(p.get("user"), "管理员账号")
    password = B.ensure(p.get("password"), "密码")
    level = str(p.get("level") or 15)

    lines = [_ENTER, f"sysname {sysname}",
             f"vlan {vlan_id}", " quit",
             f"interface Vlanif {vlan_id}",
             f" ip address {mgmt_ip} {mask}", " quit"]
    if p.get("gateway"):
        lines.append(f"ip route-static 0.0.0.0 0 {p['gateway'].strip()}")
    b.block("设备名与管理地址", lines)

    lines = [_ENTER, "aaa",
             f" local-user {user} password irreversible-cipher {password}",
             f" local-user {user} privilege level {level}",
             f" local-user {user} service-type ssh",
             " quit",
             "",
             "user-interface vty 0 4",
             " authentication-mode aaa",
             " protocol inbound ssh",
             " quit",
             "stelnet server enable",
             "rsa local-key-pair create",
             "undo telnet server enable"]
    b.block("管理员账号与 SSH", lines,
            comment="rsa local-key-pair create 会交互询问密钥长度，输入 2048；生成需数分钟。")
    b.warn("脚本中的密码是明文，交付后请立即更换。")

    if p.get("ntp"):
        b.block("NTP 时间同步", [
            _ENTER, "ntp-service enable",
            f"ntp-service unicast-server {p['ntp'].strip()}",
        ])

    ver = p.get("snmp_ver", "off")
    if ver == "v2c" and p.get("snmp_community"):
        lines = [_ENTER, "snmp-agent",
                 f"snmp-agent community read simple {p['snmp_community'].strip()}",
                 "snmp-agent sys-info version v2c"]
        if p.get("snmp_trap"):
            lines.append(
                f"snmp-agent target-host trap address udp-domain {p['snmp_trap'].strip()}"
                f" params securityname {p['snmp_community'].strip()} v2c")
        b.block("SNMP（v2c）", lines)
        b.warn("部分软件版本不接受 simple 明文团体字，此时请改用 cipher 并交互输入。")
    elif ver == "v3":
        b.block("SNMP（v3）", [
            _ENTER, "snmp-agent",
            "snmp-agent sys-info version v3",
            "snmp-agent group v3 SCG-GROUP privacy",
            "snmp-agent usm-user v3 scguser SCG-GROUP",
        ], comment="用户认证与加密密码请交互输入，不要明文写进脚本。")

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


# --------------------------------------------------------------------------
# 高可用
# --------------------------------------------------------------------------

def _stacking(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    member = B.ensure(p.get("member_id"), "成员号")
    priority = B.ensure(p.get("priority"), "优先级")
    p1 = ports.expand(p.get("port1", ""), VENDOR.key)
    p2 = ports.expand((p.get("port2") or "").strip(), VENDOR.key)
    if not p1:
        raise ValueError("必须指定堆叠口 1。")

    b.block("设置堆叠优先级", [
        _ENTER, f"stack slot 0 priority {priority}",
    ], comment="slot 0 为本机槽位号；华为 iStack 的槽位号需先用 display stack 确认。")

    lines = [_ENTER]
    for idx, name in enumerate(p1, start=1):
        lines += [f"interface stack-port 0/{idx}",
                  f" port interface {name} enable", " quit"]
    if p2:
        for idx, name in enumerate(p2, start=len(p1) + 1):
            lines += [f"interface stack-port 0/{idx}",
                      f" port interface {name} enable", " quit"]
    b.block("绑定堆叠物理端口", lines,
            comment="华为堆叠端口编号与物理端口需一一对应，请按机型手册核对。")

    b.block("保存并重启", [_ENTER, "save"], comment="保存后执行 reboot 使堆叠配置生效。")
    b.warn("华为 iStack 的具体命令随型号（S5700 / S6720 / S12700）差异较大，下发前请核对手册。")
    b.warn("成员号变更与堆叠激活都会重启设备。")
    if not p2:
        b.note("只配了一条堆叠链路，链路故障可能导致堆叠分裂，建议配置第二条。")
    return b.done()


def _gateway(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    if p.get("proto") == "hsrp":
        raise ValueError("华为不支持 HSRP，请选择 VRRP。")

    vlan_id = B.ensure(p.get("vlan_id"), "接口 VLAN")
    group = B.ensure(p.get("group"), "备份组号")
    vip = B.ensure(p.get("virtual_ip"), "虚拟网关 IP")
    iface_ip = B.ensure(p.get("interface_ip"), "本机接口 IP")
    mask = int(p.get("mask") or 24)
    priority = B.ensure(p.get("priority"), "优先级")

    lines = [_ENTER, f"interface Vlanif {vlan_id}",
             f" ip address {iface_ip} {mask}",
             f" vrrp vrid {group} virtual-ip {vip}",
             f" vrrp vrid {group} priority {priority}"]
    track = (p.get("track") or "").strip()
    if track:
        trk = ports.expand(track, VENDOR.key)[0]
        lines.append(f" vrrp vrid {group} track interface {trk} reduced 30")
    if B.truthy(p.get("preempt")):
        lines.append(f" vrrp vrid {group} preempt-mode timer delay 5")
    lines.append(" quit")
    b.block("配置 VRRP 虚拟网关", lines,
            comment="华为的端口联动直接写在 vrrp 命令里，不需要先建 track 项。")

    b.block("保存配置", _SAVE, comment="save 命令会提示确认，按 Y 继续。")
    return b.done()


# --------------------------------------------------------------------------
# 第二批：二层安全
# --------------------------------------------------------------------------

def _port_isolation(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group") or "1", "隔离组号")
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写需要隔离的端口。")

    b.block("端口加入隔离组",
            [_ENTER] + _if_blocks(port_list, [f"port-isolate enable group {group}"]),
            comment="华为不需要预先创建隔离组，直接在接口下指定组号即可，取值 1-64。")
    b.block("保存配置", _SAVE)

    uplink = ports.expand(p.get("uplink", ""), VENDOR.key)
    if uplink:
        b.note(f"上行口 {', '.join(uplink)} 未加入隔离组，隔离口与它之间可以正常通信。")
    else:
        b.warn("没有指定上行口。请确认至少有一个端口不在隔离组内，否则隔离口无法与外部通信。")
    if B.truthy(p.get("l3_isolate")):
        b.warn("华为的端口隔离只阻挡隔离组内的二层互通，三层流量仍可经网关互访。"
               "要连三层一起挡，需要配合 MFF（组播转发模式）或 ACL，本工具不生成。")
    b.note("管理口不支持 port-isolate enable。")
    return b.done()


def _port_security(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写受控端口。")
    max_mac = B.ensure(p.get("max_mac") or "3", "最大 MAC 数")
    action = p.get("action", "restrict")
    vlan = str(p.get("vlan") or "").strip()
    aging = str(p.get("aging") or "").strip()

    body = ["port-security enable",
            f"port-security max-mac-num {max_mac}",
            f"port-security protect-action {action}"]
    if aging:
        body.append(f"port-security aging-time {aging}")
    if B.truthy(p.get("sticky")):
        body.append("port-security mac-address sticky")

    macs = B.parse_macs(p.get("macs", ""))
    for mac in macs:
        if vlan:
            body.append(f"port-security mac-address {B.mac_dashed(mac)} vlan {vlan}")
        else:
            body.append(f"port-security mac-address {B.mac_dashed(mac)}")

    b.block("配置端口安全", [_ENTER] + _if_blocks(port_list, body),
            comment="华为的端口安全是在接口下开启的，没有全局开关。"
                    "protect-action 取值 protect / restrict / shutdown。")
    b.block("保存配置", _SAVE)

    if macs and not vlan:
        b.note("手工绑定的 MAC 没有指定 VLAN，已按「该端口任意 VLAN 都放行」生成；"
               "要限定 VLAN 请在参数里填上。")
    if action == "shutdown":
        b.warn("违例动作选了 shutdown，端口一旦触发违例会直接关闭，需人工干预才能恢复。")
    b.note("查看运行情况：display port-security；查看违例记录：display port-security intrusion。")
    return b.done()


def _storm_control(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写受控端口。")
    value = B.ensure(p.get("value") or "10", "阈值")
    mode = p.get("mode", "percent")

    if mode == "percent":
        if not value.isdigit() or not 0 < int(value) <= 100:
            raise ValueError("百分比阈值必须是 1-100 的整数。")
        high = int(value)
        low = max(1, high // 2)
        pct = f"min-rate percent {low} max-rate percent {high}"
        pps = None
    else:
        if not value.isdigit():
            raise ValueError("pps 阈值必须是整数。")
        high = int(value)
        low = max(1, high // 2)
        pct = None
        pps = f"min-rate {low} max-rate {high}"

    body: list[str] = []
    if B.truthy(p.get("bcast")):
        body.append(f"storm control broadcast {pct or pps}")
    if B.truthy(p.get("mcast")):
        body.append(f"storm control multicast {pct or pps}")
    if B.truthy(p.get("ucast")):
        body.append(f"storm control unknown-unicast {pct or pps}")
    if not body:
        raise ValueError("广播 / 组播 / 未知单播至少要勾一项。")
    body.append("storm control action block")

    b.block("配置风暴控制", [_ENTER] + _if_blocks(port_list, body),
            comment="华为必须同时给低阈值和高阈值两个值，只给一个会报错；"
                    "工具按「高阈值的一半」自动算低阈值。"
                    "超过了只丢包、不关端口，动作是 storm control action block。")
    b.block("保存配置", _SAVE)
    b.note("查看运行情况：display storm-control。")
    b.warn("部分 VRP V5 版本（如早期 S5700）的命令写作 storm-control（带连字符）"
           "且单位为 min-rate / max-rate 两个纯数字，下发前请用 storm control ? 确认本机写法。")
    return b.done()


def _loop_guard(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans_spec = (p.get("vlans") or "").strip()
    interval = B.ensure(p.get("interval") or "10", "检测周期")
    action = p.get("action", "shutdown")
    vlan_arg = "all" if not vlans_spec else B.compact_vlans(
        B.parse_vlans(vlans_spec), " ", " to ")

    lines = [_ENTER, "loop-detection enable",
             f"loop-detection enable vlan {vlan_arg}",
             f"loop-detection interval-time {interval}"]
    b.block("全局开启 Loop Detection", lines,
            comment="必须先开全局开关，再开 VLAN 开关，两步都做了才生效。"
                    "全局开启后每个接口默认都参与检测。")

    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if port_list:
        if action == "shutdown":
            act = "port-shutdown"
        elif action == "block":
            act = "block"
        else:
            act = "trap"
        b.block("按端口设置处理动作",
                [_ENTER] + _if_blocks(port_list, [f"loop-detection mode {act}"]),
                comment="检测到环路后的动作：port-shutdown 关端口 / block 阻塞 / trap 只上报告警。")

    if B.truthy(p.get("bpdu_guard")):
        b.block("全局开启 BPDU 保护", [_ENTER, "stp bpdu-protection"],
                comment="边缘端口一旦收到 BPDU，说明下面接的不是终端而是交换机，"
                        "设备会直接把该端口关闭。")
        edge = ports.expand(p.get("edge_ports", ""), VENDOR.key)
        if edge:
            b.block("设置边缘端口",
                    [_ENTER] + _if_blocks(edge, ["stp edged-port enable"]),
                    comment="只有边缘端口才受 BPDU 保护管辖；上行口不要配。")
        else:
            b.warn("开了 BPDU 保护但没有指定边缘端口，保护不会生效。")

    b.block("保存配置", _SAVE)
    b.warn("Loop Detection 与 ERPS、RRPP、SEP、Smart Link、STP/RSTP/MSTP 冲突。"
           "跑了环网协议的接口，要先在接口下执行 loop-detection disable 把它关掉。")
    b.warn("部分 V200 版本的处理动作关键字是 loop-detection mode "
           "{block | nolearning | shutdown | quitvlan | trap}，"
           "本工具生成的是 port-shutdown 写法，下发前请用 loop-detection mode ? 确认。")
    return b.done()


# --------------------------------------------------------------------------
# 第二批：接入安全
# --------------------------------------------------------------------------

def _dhcp_snooping(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    b.block("全局开启 DHCP 与 DHCP Snooping",
            [_ENTER, "dhcp enable", "dhcp snooping enable"],
            comment="dhcp enable 是总开关，漏了它 DHCP Snooping 不生效。")

    lines = [_ENTER]
    for v in vlans:
        lines += [f"vlan {v}", " dhcp snooping enable", " quit"]
    b.block("在 VLAN 内开启", lines,
            comment="华为的 DHCP Snooping 要在 VLAN 视图下逐个开启。")

    trust = ports.expand(p.get("trust_ports", ""), VENDOR.key)
    if trust:
        b.block("指定信任接口",
                [_ENTER] + _if_blocks(trust, ["dhcp snooping trusted"]),
                comment="只有信任接口上收到的 DHCP 应答才会被转发，其余一律丢弃。")
    else:
        b.warn("没有指定信任接口，全网 DHCP 应答都会被丢弃，客户端拿不到地址。")

    b.block("保存配置", _SAVE)
    if B.truthy(p.get("check_mac")):
        b.note("华为的「MAC 校验」开关名称随版本不同（例如 dhcp snooping check mac-address enable），"
               "本工具没有生成，请用 dhcp snooping check ? 确认后再配。")
    b.note("DHCP Snooping 绑定表是动态 ARP 检测与 IP 源防护的判断依据，建议一起配。")
    b.note("配完后终端要重新获取一次地址（禁用再启用网卡）才会生成绑定表。")
    return b.done()


def _arp_guard(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    lines = [_ENTER, "dhcp enable", "dhcp snooping enable"]
    for v in vlans:
        lines += [f"vlan {v}",
                  " dhcp snooping enable",
                  " arp anti-attack check user-bind enable",
                  " quit"]
    b.block("开启动态 ARP 检测（DAI）", lines,
            comment="DAI 靠 DHCP Snooping 绑定表判断报文是否合法，"
                    "所以 dhcp enable、dhcp snooping enable 一个都不能少。")

    trust = ports.expand(p.get("trust_ports", ""), VENDOR.key)
    if trust:
        b.block("指定信任接口",
                [_ENTER] + _if_blocks(trust, ["dhcp snooping trusted"]),
                comment="上行口必须设为信任接口，否则来自网关的正常 ARP 也会被丢弃。")
    else:
        b.warn("没有指定信任接口，上行链路的 ARP 报文会被误拦，请务必设置。")

    if B.truthy(p.get("ip_source_guard")):
        users = ports.expand(p.get("user_ports", ""), VENDOR.key)
        if not users:
            b.warn("没有填写用户接入口，IP 源防护无法逐口下发，已跳过。")
        else:
            b.block("用户口开启 IP 源防护",
                    [_ENTER] + _if_blocks(users, ["ip source check user-bind enable"]),
                    comment="按绑定表校验源 IP 与源 MAC，终端私自改 IP 后报文会被直接丢弃。")
        b.warn("IP 源防护依赖绑定表。终端若用静态 IP，必须先用 user-bind static 建表，"
               "否则它会连不上网。")

    b.block("保存配置", _SAVE)
    b.note("查看丢弃统计：display arp anti-attack statistics check user-bind interface。")
    return b.done()


# --------------------------------------------------------------------------
# 第二批：三层 / 组播
# --------------------------------------------------------------------------

def _igmp_snooping(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    lines = [_ENTER, "igmp-snooping enable"]
    for v in vlans:
        lines += [f"vlan {v}", " igmp-snooping enable", " quit"]
    b.block("开启 IGMP Snooping", lines,
            comment="先开全局，再逐 VLAN 开，两步都要做。")

    if B.truthy(p.get("querier")):
        ip = B.ensure(p.get("querier_ip"), "查询器源地址")
        lines = [_ENTER]
        for v in vlans:
            lines += [f"vlan {v}",
                      " igmp-snooping querier enable",
                      f" igmp-snooping querier source-address {ip}",
                      " quit"]
        b.block("配置 IGMP 查询器", lines,
                comment="VLAN 内没有三层组播设备时，由交换机自己发查询报文维持组播表项。")

    router_ports = ports.expand(p.get("router_ports", ""), VENDOR.key)
    if router_ports:
        lines = [_ENTER]
        for v in vlans:
            lines.append(f"interface Vlanif {v}")
            for name in router_ports:
                lines.append(f" igmp-snooping router-port interface {name}")
            lines.append(" quit")
        b.block("静态指定路由器端口", lines,
                comment="华为的三层接口叫 Vlanif，注意与 H3C 的 Vlan-interface 区分。")

    if B.truthy(p.get("fast_leave")):
        lines = [_ENTER]
        for v in vlans:
            lines += [f"vlan {v}", " igmp-snooping prompt-leave", " quit"]
        b.block("开启快速离开", lines,
                comment="华为把「快速离开」叫 prompt-leave，收到离开报文立即停止转发。")

    b.block("保存配置", _SAVE)
    if B.truthy(p.get("querier")):
        b.warn("同一个 VLAN 里只应存在一个 IGMP 查询器，多台设备都开容易互相干扰。")
    return b.done()


def _ospf(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    process = B.ensure(p.get("process") or "1", "进程号")
    router_id = B.ensure(p.get("router_id"), "Router ID")
    nets = B.parse_routes_area(p.get("networks", ""))
    if not nets:
        raise ValueError("请至少宣告一个网段。")

    b.block("启用 OSPF 并指定 Router ID",
            [_ENTER, f"ospf {process} router-id {router_id}", " quit"],
            comment="Router ID 建议手工指定成 Loopback 地址，不要依赖设备自动选举。")

    lines = [_ENTER, f"ospf {process}"]
    for net, plen, area in nets:
        lines += [f" area {area}",
                  f"  network {net} {B.prefix_to_wildcard(plen)}",
                  "  quit"]
    lines.append(" quit")
    b.block("宣告网段", lines,
            comment="VRP 的 area 视图下 network 用反掩码（通配符掩码）。")

    if p.get("auth") == "md5":
        key = B.ensure(p.get("auth_key"), "验证密钥")
        areas: list[str] = []
        for _, _, area in nets:
            if area not in areas:
                areas.append(area)
        lines = [_ENTER, f"ospf {process}"]
        for area in areas:
            lines += [f" area {area}", "  authentication-mode md5", "  quit"]
        lines.append(" quit")
        b.block("开启区域验证", lines,
                comment="区域验证要在同一区域的所有设备上保持一致，否则邻居建不起来。")
        b.warn(f"MD5 验证密钥 {key} 是明文写在脚本里的，下发后请及时改成密文保存。")

    passive = B.split_ifaces(p.get("passive"))
    if passive:
        lines = [_ENTER, f"ospf {process}"]
        lines += [f" silent-interface {name}" for name in passive]
        lines.append(" quit")
        b.block("设置被动接口", lines,
                comment="被动接口不发 Hello，但网段照样宣告出去，适合接终端的接口。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "display ospf peer", "display ospf routing"],
            comment="先看邻居是不是 Full，再看路由有没有进表。")
    b.note("两端设备的区域号、验证方式、Hello / Dead 定时器必须一致，否则建立不了邻居。")
    return b.done()


# --------------------------------------------------------------------------
# 第二批：运维
# --------------------------------------------------------------------------

def _qos_rate(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写限速端口。")
    cir = B.ensure(p.get("cir"), "承诺速率")
    if not cir.isdigit():
        raise ValueError("承诺速率必须是整数（单位 kbps）。")
    direction = p.get("direction", "inbound")
    cbs = str(p.get("cbs") or "").strip()

    cmd = f"qos lr {direction} cir {cir}"
    if cbs:
        if not cbs.isdigit():
            raise ValueError("突发尺寸必须是整数（单位字节）。")
        cmd += f" cbs {cbs}"
    b.block("端口限速", [_ENTER] + _if_blocks(port_list, [cmd]),
            comment="qos lr 是接口级物理限速。cbs 不给就用设备默认值。")
    b.block("核对限速结果", [_ENTER, "display qos lr interface"],
            comment="确认 CIR 已经按要求生效。")
    b.block("保存配置", _SAVE)
    b.note(f"当前限制的是{('入方向（用户上行）' if direction == 'inbound' else '出方向（用户下行）')}。")
    b.note("精细化限速可以用 qos car inbound cir <kbps> cbs <bytes>，按 ACL 匹配特定流量。")
    return b.done()


def _inspect(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    if not any(B.truthy(p.get(k)) for k in
               ("basic", "interface", "l2", "l3", "security", "hardware", "logs")):
        raise ValueError("至少勾选一类巡检内容。")

    if B.truthy(p.get("basic")):
        b.block("设备信息与资源", [
            "display version", "display device", "display device manufacture-info",
            "display clock", "display cpu-usage", "display memory-usage",
            "display boot-loader", "display startup", "display fan",
            "display power", "display temperature all",
        ], comment="开局先留一份基线，出问题时拿它对比。")

    if B.truthy(p.get("interface")):
        b.block("接口与流量", [
            "display interface brief", "display interface",
            "display counters inbound interface", "display counters outbound interface",
            "display counters rate inbound interface", "display counters rate outbound interface",
            "display interface | include error", "display interface transceiver verbose",
        ], comment="看错包、丢包和速率，是判断链路质量最快的一组命令。")

    if B.truthy(p.get("l2")):
        b.block("二层转发表", [
            "display vlan", "display vlan summary",
            "display mac-address", "display mac-address flapping record",
            "display eth-trunk", "display stp brief", "display stp root",
            "display lldp neighbor brief",
        ], comment="mac-address flapping record 直接列出 MAC 漂移记录，"
                   "是定位环路最直接的线索。")

    if B.truthy(p.get("l3")):
        b.block("三层与地址", [
            "display ip routing-table", "display ip routing-table statistics",
            "display arp all", "display ip interface brief",
            "display dhcp server ip-in-use", "display dhcp relay statistics",
            "display vrrp brief", "display ospf peer", "display ospf routing",
        ], comment="先看路由表里有没有你要的那条，再看 ARP 学没学到对端。")

    if B.truthy(p.get("security")):
        b.block("安全与接入", [
            "display dhcp snooping binding", "display dhcp snooping statistics",
            "display arp anti-attack statistics check user-bind interface",
            "display port-security", "display port-security intrusion",
            "display ip source check user-bind", "display dot1x",
            "display acl all", "display traffic-filter applied-record",
        ], comment="命中计数长期为 0 的规则，往往说明它根本没生效。")

    if B.truthy(p.get("hardware")):
        b.block("硬件与光模块", [
            "display transceiver interface", "display transceiver verbose",
            "display poe interface", "display poe power-state",
            "display power-supply", "display elabel",
        ], comment="光模块收发功率是排查「链路 up 但丢包」的关键数据。")

    if B.truthy(p.get("logs")):
        b.block("日志与告警", [
            "display logbuffer", "display logbuffer summary",
            "display trapbuffer", "display alarm active",
            "display reset-reason", "display cpu-usage history",
        ], comment="异常重启原因和日志缓冲是定位设备自身问题的入口。")

    if B.truthy(p.get("export")):
        b.block("一键收集诊断信息", ["display diagnostic-information"],
                comment="这个命令耗时较长（可能几分钟），会生成一份综合诊断文件，"
                        "提交给技术支持时非常有用。")

    b.block("保存当前配置（便于与巡检输出对照）", _SAVE)
    b.note("以上全部是查询命令，不会改动设备配置，可以放心执行。")
    b.warn("display diagnostic-information 与部分统计清零类命令会占用较多 CPU，"
           "请在业务低谷期执行。")
    return b.done()


def _backup(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    server = B.ensure(p.get("server"), "服务器地址")
    filename = B.ensure(p.get("filename"), "文件名")
    proto = p.get("proto", "tftp")
    action = p.get("action", "backup")

    if B.truthy(p.get("save_first")):
        b.block("先保存当前配置", [_ENTER, "save"],
                comment="save 会交互式询问是否确认，按 Y 即可。"
                        "不先保存的话，备份走的是上一次保存过的配置。")

    if proto == "ftp":
        if not str(p.get("user") or "").strip() or not str(p.get("password") or "").strip():
            raise ValueError("FTP 方式需要填写用户名和密码。")
        b.warn("华为的 FTP 备份需要先在设备上配好 FTP 客户端源地址与账号，"
               "且脚本里不生成明文密码，登录时请手工输入。")

    verb = "put" if action == "backup" else "get"
    src = "flash:/vrpcfg.zip" if action == "backup" else filename
    dst = filename if action == "backup" else "flash:/vrpcfg.zip"
    if proto == "tftp":
        cmd = [f"tftp {server} {verb} {src} {dst}"]
    else:
        cmd = [f"ftp {server}", f"{verb} {src} {dst}"]
    b.block("备份配置到服务器" if action == "backup" else "从服务器恢复配置",
            [_ENTER] + cmd,
            comment="华为的启动配置文件通常是 flash:/vrpcfg.zip，"
                    "部分型号为 flash:/vrpcfg.cfg，请先用 dir flash:/ 确认。")

    if action == "backup":
        b.block("确认配置已保存", [_ENTER, "display saved-configuration"],
                comment="确认当前运行配置与已保存配置一致，避免备份到过期版本。")
    else:
        b.warn("恢复配置需要重启设备才完全生效，且会覆盖现有配置，"
               "请在维护窗口内操作，并提前做好当前配置的二次备份。")

    b.note(f"服务器地址 {server}，文件名 {filename}，传输方式 {proto.upper()}。")
    return b.done()


def _syslog(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    level = p.get("level", "informational")
    buffer_size = str(p.get("buffer") or "512").strip()
    server = str(p.get("server") or "").strip()
    ntp = str(p.get("ntp") or "").strip()
    timezone = str(p.get("timezone") or "").strip()

    lines = [_ENTER, "info-center enable",
             f"info-center logbuffer size {buffer_size}"]
    zone = B.parse_timezone(timezone)
    if zone:
        lines.append(B.h3c_timezone(zone))
    elif timezone:
        b.warn(f"时区「{timezone}」没解析出来，已跳过。请写成 UTC+08:00 或 BJ+8 这种形式。")
    b.block("开启信息中心", lines,
            comment="info-center enable 是日志功能的总开关，"
                    "logbuffer size 决定本地能存多少条日志。")

    if B.truthy(p.get("timestamps")):
        b.block("日志加时间戳", [
            _ENTER,
            "info-center timestamp loghost date",
            "info-center timestamp log date",
        ], comment="没有时间戳的日志在排障时几乎用不上，这一条建议保持开启。")

    if server:
        lines = [_ENTER, f"info-center loghost {server}"]
        if B.truthy(p.get("console")):
            lines.append("info-center console channel console")
        b.block("配置日志服务器", lines,
                comment=f"华为的日志级别在 info-center source 命令里按模块单独设置，"
                        f"默认送出的级别已经覆盖 {level}，如需收窄请用 "
                        f"info-center source default channel loghost log level {level}。")

    if ntp:
        b.block("NTP 时间同步", [_ENTER, f"ntp-service unicast-server {ntp}"],
                comment="华为从 V200R003 起 NTP 默认就是开启的，只配服务器地址即可。"
                        "时钟不准，日志的先后顺序就不可信，NTP 要和日志一起配。")

    b.block("保存配置", _SAVE)
    b.block("验证日志是否送达",
            [_ENTER, "display info-center", "display logbuffer summary"],
            comment="配完在设备上制造一条日志（比如插拔一个端口），"
                    "去服务器上确认收到了。")
    return b.done()


# --------------------------------------------------------------------------
# 第三批：动态路由与 MPLS VPN
# --------------------------------------------------------------------------

def _l3_ifaces(p, vendor_key) -> list[str]:
    """把「三层接口 VLAN」和「三层物理口」两个参数合成接口名列表。"""
    out: list[str] = []
    for v in B.split_ifaces(p.get("svlan")):
        out.append(B.l3_iface(vendor_key, v))
    out.extend(B.resolve_ifaces(p.get("ports"), vendor_key))
    return out


def _isis(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    process = B.ensure(p.get("process") or "1", "进程号")
    net = B.ensure(p.get("net"), "NET 地址")
    level = p.get("level", "level-2")

    b.block("启用 IS-IS 并配置 NET", [
        _ENTER, f"isis {process}", f" is-level {level}",
        f" cost-style {p.get('cost_style', 'wide')}",
        f" network-entity {net}", " quit",
    ], comment="官方建议 cost-style、is-level、network-entity 三条一起改时"
               "把 network-entity 放最后，避免配置顺序不对导致 IS-IS 进程重启。")

    ifaces = _l3_ifaces(p, VENDOR.key)
    if ifaces:
        body = [f"isis enable {process}"]
        if p.get("circuit_level", "follow") != "follow":
            body.append(f"isis circuit-level {p['circuit_level']}")
        if B.truthy(p.get("auth")):
            key = B.ensure(p.get("auth_key"), "验证密钥")
            body.append(f"isis authentication-mode md5 plain {key}")
        b.block("接口使能 IS-IS", [_ENTER] + _if_blocks(ifaces, body),
                comment="只能建单层邻接时用 isis circuit-level 限制。"
                        "华为还支持 hmac-sha256，写法是 "
                        "isis authentication-mode hmac-sha256 key-id 1 cipher <密钥>。")
    else:
        b.warn("没有填三层接口，IS-IS 起来了也建不了邻接。")

    if B.truthy(p.get("import_direct")):
        b.block("引入直连路由", [
            _ENTER, f"isis {process}", " address-family ipv4",
            "  import-route direct", "  quit", " quit",
        ], comment="import-route 放在 address-family ipv4 视图里。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "display isis peer", "display isis route", "display isis lsdb"],
            comment="先看邻接是不是 Up，再看路由表里有没有学到对端网段。")
    b.note("同一台设备只能有一个 IS-IS 实例运行在 Level-2（含 Level-1-2）。")
    return b.done()


def _bgp(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    local_as = B.ensure(p.get("local_as") or "65001", "本机 AS 号")
    router_id = B.ensure(p.get("router_id"), "Router ID")
    peers = B.parse_peers(p.get("peers", ""))
    if not peers:
        raise ValueError("请至少填写一个 BGP 邻居。")
    nets = B.parse_networks(p.get("networks", ""))

    lines = [_ENTER, f"bgp {local_as}", f" router-id {router_id}"]
    for peer in peers:
        lines.append(f" peer {peer.ip} as-number {peer.asn}")
        if peer.desc:
            lines.append(f" peer {peer.ip} description {peer.desc}")
    if B.truthy(p.get("md5")):
        key = B.ensure(p.get("md5_key"), "MD5 密钥")
        for peer in peers:
            lines.append(f" peer {peer.ip} password simple {key}")
    ka = str(p.get("keepalive") or "").split()
    if len(ka) == 2 and all(x.isdigit() for x in ka):
        for peer in peers:
            lines.append(f" peer {peer.ip} timer keepalive {ka[0]} hold {ka[1]}")

    lines += [" ipv4-family unicast"]
    for peer in peers:
        lines.append(f"  peer {peer.ip} enable")
    for net, plen in nets:
        lines.append(f"  network {net} {B.prefix_to_dotted(plen)}")
    if B.truthy(p.get("import_direct")):
        lines.append("  import-route direct")
    lines += ["  quit", " quit"]
    b.block("配置 BGP 与邻居", lines,
            comment="华为的地址族关键字是 ipv4-family（不是 address-family），"
                    "而且必须进 ipv4-family unicast 再 peer enable 一次。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "display bgp peer", "display bgp routing-table"],
            comment="State 要是 Established，路由表里要有你要的网段。")
    if B.truthy(p.get("md5")):
        b.warn("TCP MD5 密钥在脚本里是明文，两端必须一致，不一致会一直卡在 Connect。")
    b.note("IBGP 建议用 Loopback 建邻居，并配 peer <地址> connect-interface LoopBack0。")
    return b.done()


def _mplsvpn(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    lsr = B.ensure(p.get("lsr_id"), "LSR ID")
    as_num = B.ensure(p.get("as_number") or "100", "本域 AS 号")
    pe_peer = B.ensure(p.get("pe_peer"), "对端 PE 的 Loopback")
    core_vlan = B.ensure(p.get("core_svlan"), "公网侧 VLAN")
    core_ip = B.ensure(p.get("core_ip"), "公网侧本机地址")
    core_mask = str(p.get("core_mask") or "30")
    vrf = B.ensure(p.get("vrf_name"), "VPN 实例名")
    rd = B.ensure(p.get("rd"), "RD")
    rt_in = B.ensure(p.get("rt_import"), "RT 导入值")
    rt_out = B.ensure(p.get("rt_export"), "RT 导出值")
    ce_vlan = B.ensure(p.get("ce_svlan"), "接 CE 的 VLAN")
    ce_ip = B.ensure(p.get("ce_ip"), "CE 侧本机地址")
    ce_mask = str(p.get("ce_mask") or "24")
    ce_as = B.ensure(p.get("ce_as") or "65410", "CE 的 AS 号")
    ce_peer = B.ensure(p.get("ce_peer_ip"), "CE 的接口地址")

    loop = B.loopback_iface(VENDOR.key, 0)
    core_if = B.l3_iface(VENDOR.key, core_vlan)
    ce_if = B.l3_iface(VENDOR.key, ce_vlan)

    b.block("先建好 LoopBack", [
        _ENTER, f"interface {loop}", f" ip address {lsr} 32", " quit",
    ], comment="LSR ID 用 Loopback 地址最稳，只要它不 down，LDP 会话就不会断。"
               "华为的环回口写法是 LoopBack0（大写 B）。")

    b.block("开启 MPLS 与 LDP", [
        _ENTER, f"mpls lsr-id {lsr}", "mpls", "mpls ldp",
        f"interface {core_if}", f" ip address {core_ip} {core_mask}",
        " mpls", " mpls ldp", " quit",
    ], comment="华为全局要打 mpls 和 mpls ldp 两条；接口下是 mpls 和 mpls ldp，"
               "注意不是 H3C 的 mpls enable。接 CE 的接口不要开 MPLS。")

    b.block("创建 VPN 实例", [
        _ENTER, f"ip vpn-instance {vrf}", " ipv4-family",
        f"  route-distinguisher {rd}",
        f"  vpn-target {rt_out} export-extcommunity",
        f"  vpn-target {rt_in} import-extcommunity",
        "  quit", " quit",
    ], comment="华为的 RD / RT 要配在 ip vpn-instance 的 ipv4-family 子视图里，"
               "直接配在实例视图下会报错。")

    b.block("把 CE 接口绑定到 VPN 实例", [
        _ENTER, f"interface {ce_if}",
        f" ip binding vpn-instance {vrf}",
        f" ip address {ce_ip} {ce_mask}", " quit",
    ], comment="注意顺序：ip binding 会清掉接口上原有的 IP 配置，"
               "所以必须先绑实例再配地址，反了地址就没了。")

    b.block("MP-BGP 传递 VPN 路由", [
        _ENTER, f"bgp {as_num}",
        f" peer {pe_peer} as-number {as_num}",
        f" peer {pe_peer} connect-interface {loop}",
        " ipv4-family vpnv4",
        "  policy vpn-target",
        f"  peer {pe_peer} enable",
        "  quit",
        f" ipv4-family vpn-instance {vrf}",
        f"  peer {ce_peer} as-number {ce_as}",
        "  import-route direct",
        "  quit",
        " quit",
    ], comment="ipv4-family vpnv4 管 PE 之间传 VPN 路由；"
               "ipv4-family vpn-instance 管 PE 与 CE 之间的私网邻居。"
               "policy vpn-target 是默认行为，显式写出来便于理解。")

    b.block("保存配置", _SAVE)
    b.block("分段核对", [
        _ENTER, "display mpls ldp peer", "display mpls lsp",
        "display ip vpn-instance", "display bgp vpnv4 all peer",
        f"display bgp vpnv4 vpn-instance {vrf} routing-table",
    ], comment="按顺序查：LDP 邻居通了没、标签有没有下发、VPN 实例建了没、"
               "MP-BGP 邻居起来没、VPN 路由收没收到。")
    b.warn("MPLS 的前提是公网侧 IGP 已经跑通，而且两端的 Loopback 都要被宣告进 IGP，"
           "否则 LDP 会话建不起来。建议先配 OSPF 或 IS-IS 那两条场景。")
    b.warn("PE 与 CE 之间的接口不要再开 MPLS，也不要绑两个 VPN 实例。")
    return b.done()


# --------------------------------------------------------------------------
# 第四批：接入认证 / 供电 / QoS / IPv6
# --------------------------------------------------------------------------

def _dot1x(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    ip = B.ensure(p.get("radius_ip"), "RADIUS 服务器地址")
    key = B.ensure(p.get("radius_key"), "RADIUS 共享密钥")
    domain = B.ensure(p.get("domain"), "认证域名称")
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写受控端口。")

    b.block("配置 RADIUS 服务器模板", [
        _ENTER, "radius-server template SCG-RADIUS",
        f" radius-server authentication {ip} 1812",
        f" radius-server accounting {ip} 1813",
        f" radius-server shared-key cipher {key}",
        " quit",
    ], comment="华为要先建 RADIUS 服务器模板，再让认证域去引用它。")
    b.warn("共享密钥在脚本里是明文，交付后请改成密文保存或立即更换。")

    b.block("配置认证方案与认证域", [
        _ENTER, "aaa",
        " authentication-scheme SCG-AUTH",
        "  authentication-mode radius", "  quit",
        f" domain {domain}",
        "  authentication-scheme SCG-AUTH",
        "  radius-server SCG-RADIUS",
        "  quit",
        " quit",
    ], comment="认证方案决定用哪种方式认证，认证域把方案和 RADIUS 模板绑在一起。"
               "终端上填的账号要带上 @域名 后缀，或者把该域设为默认域。")

    body = ["dot1x enable",
            f"dot1x port-method {p.get('port_method', 'macbased')}",
            f"dot1x port-control {p.get('port_control', 'auto')}"]
    if p.get("guest_vlan"):
        body.append(f"dot1x guest-vlan {p['guest_vlan']}")
    if p.get("max_user"):
        body.append(f"dot1x max-user {p['max_user']}")
    b.block("端口开启 802.1X", [_ENTER] + _if_blocks(port_list, body),
            comment="华为是接口下 dot1x enable，没有全局开关。")

    b.block("保存配置", _SAVE)
    b.block("核对认证状态",
            [_ENTER, "display dot1x", "display access-user"],
            comment="先看端口有没有在跑 802.1X，再看在线用户列表。")
    if B.truthy(p.get("mac_bypass")):
        b.note("华为的 MAC 旁路认证要在认证模板里配，命令随版本差异较大，"
               "本工具没有生成，请用 ? 确认后手工补配。")
    b.warn("华为从 V600 版本起改用 NAC 认证模板模型（authentication-profile），"
           "接口下不再写 dot1x enable，而是绑认证模板。"
           "本工具生成的是 V200 的写法，下发前请先用 ? 确认本机属于哪套模型。")
    return b.done()


def _poe(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写供电端口。")
    action = p.get("action", "enable")

    body: list[str] = []
    if action == "disable":
        body.append("undo poe enable")
    else:
        body.append("poe enable")
        body.append(f"poe priority {p.get('priority', 'high')}")

    b.block("配置 PoE 供电", [_ENTER] + _if_blocks(port_list, body),
            comment="poe priority 决定整机供电不足时的取舍顺序："
                    "critical 最后断电，low 最先断电。")
    b.block("核对供电状态",
            [_ENTER, "display poe power", "display poe interface"],
            comment="确认端口在供电、功率是多少、有没有超预算。")
    b.block("保存配置", _SAVE)
    if p.get("max_power"):
        b.note(f"单口功率上限填了 {p['max_power']} 但本工具没有生成——"
               f"华为的限制命令随型号差异较大（poe power / poe power-limit），"
               f"请用 ? 确认本机写法后手工补配。")
    if action == "disable":
        b.warn("关闭 PoE 会让 AP、摄像头这类设备直接断电，确认不是生产设备再执行。")
    return b.done()


def _qos_priority(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写生效端口。")
    trust = p.get("trust", "dscp")

    if trust == "none":
        b.block("不信任报文优先级",
                [_ENTER] + _if_blocks(port_list, ["undo trust"]),
                comment="取消信任后设备改用端口优先级给所有流量兜底。")
    else:
        keyword = "8021p outer" if trust == "dot1p" else "dscp"
        b.block("配置优先级信任模式",
                [_ENTER] + _if_blocks(port_list, [f"trust {keyword}"]),
                comment="华为的命令就是 trust，没有 qos 前缀，这点与 H3C / 思科都不同。"
                        "8021p 还分 inner / outer，一般信外层，缺省就是 8021p outer。")

    if p.get("acl_id"):
        acl = B.ensure(p.get("acl_id"), "ACL 编号")
        dscp = B.ensure(p.get("dscp") or "af11", "DSCP 值")
        direction = p.get("direction", "inbound")
        b.block("定义流分类与流行为", [
            _ENTER,
            "traffic classifier SCG-CLS operator and",
            f" if-match acl {acl}", " quit",
            "traffic behavior SCG-BEH",
            f" remark dscp {dscp}", " quit",
            "traffic policy SCG-POL",
            " classifier SCG-CLS behavior SCG-BEH", " quit",
        ], comment="华为把「流策略」叫 traffic policy，绑定用 classifier ... behavior ...，"
                   f"这里把匹配 ACL {acl} 的流量重标记成 DSCP {dscp}。")
        b.block("把策略应用到端口",
                [_ENTER] + _if_blocks(port_list, [f"traffic-policy SCG-POL {direction}"]),
                comment="入方向应用在接终端的口上，标记要在入口做。")
        b.note(f"ACL {acl} 需要你先建好，可以用「ACL 访问控制」那条场景生成。")

    b.block("保存配置", _SAVE)
    b.block("核对结果",
            [_ENTER, "display trust", "display traffic policy statistics interface"],
            comment="确认信任模式生效、策略有命中计数。")
    b.note("不做标记的网络里，所有报文的优先级其实都一样。")
    return b.done()


def _ipv6(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlan = B.ensure(p.get("svlan"), "三层接口 VLAN")
    addr = B.ensure(p.get("address"), "IPv6 地址")
    prefix = str(p.get("prefix") or "64")
    iface = B.l3_iface(VENDOR.key, vlan)
    routes = B.parse_routes6(p.get("routes", ""))

    lines = [_ENTER, f"interface {iface}", " ipv6 enable",
             f" ipv6 address {addr} {prefix}"]
    if B.truthy(p.get("nd_ra")):
        lines.append(" undo ipv6 nd ra halt")
    lines.append(" quit")
    b.block("配置 IPv6 接口地址", lines,
            comment="华为要先 ipv6 enable 打开接口的 IPv6 能力，再配地址。"
                    "接终端的接口要 undo ipv6 nd ra halt 才会发路由通告。")

    if routes:
        lines = [_ENTER]
        for dest, nexthop in routes:
            net, _, plen = dest.partition("/")
            lines.append(f"ipv6 route-static {net} {plen} {nexthop}")
        b.block("配置 IPv6 静态路由", lines)

    b.block("保存配置", _SAVE)
    b.block("核对结果", [
        _ENTER, "display ipv6 interface brief", "display ipv6 routing-table",
        "display ipv6 neighbors",
    ], comment="看接口地址、路由表和邻居表三项。")
    b.note("IPv6 接口会自动生成 fe80:: 开头的链路本地地址，不要禁用。")
    return b.done()


_HANDLERS = {
    "vlan_port": _vlan_port,
    "lag": _lag,
    "stacking": _stacking,
    "stp": _stp,
    "dhcp": _dhcp,
    "static_route": _static_route,
    "acl": _acl,
    "mirror": _mirror,
    "mgmt": _mgmt,
    "gateway": _gateway,
    "port_isolation": _port_isolation,
    "port_security": _port_security,
    "storm_control": _storm_control,
    "loop_guard": _loop_guard,
    "dhcp_snooping": _dhcp_snooping,
    "arp_guard": _arp_guard,
    "igmp_snooping": _igmp_snooping,
    "ospf": _ospf,
    "qos_rate": _qos_rate,
    "inspect": _inspect,
    "backup": _backup,
    "syslog": _syslog,
    "isis": _isis,
    "bgp": _bgp,
    "mplsvpn": _mplsvpn,
    "dot1x": _dot1x,
    "poe": _poe,
    "qos_priority": _qos_priority,
    "ipv6": _ipv6,
}


def generate(scenario_key: str, params: dict, scenario) -> GenResult:
    fn = _HANDLERS.get(scenario_key)
    if fn is None:
        raise ValueError(f"{VENDOR.name} 暂不支持场景：{scenario.name}")
    result = fn(params, scenario)
    for n in scenario.notes:
        result.notes.append(n)
    return result
