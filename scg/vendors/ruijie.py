"""锐捷 Ruijie RGOS（10.x / 11.x）命令生成。

锐捷 RGOS 与思科 IOS 高度相似，主要差异：
接口名带空格（GigabitEthernet 0/1）、Trunk 放通 VLAN 需带 add、
聚合用 AggregatePort + port-group、设备虚拟化叫 VSU。
"""
from __future__ import annotations

from .. import ports
from ..model import GenResult, Vendor
from . import base as B

VENDOR = Vendor(
    key="ruijie", name="锐捷 Ruijie", platform="RGOS 10.x / 11.x", accent="#F59E0B",
    port_hint="接口编号形如 GigabitEthernet 0/1，名称与编号之间有空格；"
              "万兆为 TenGigabitEthernet 0/49；三层接口为 Vlan 10。",
)

_ENTER = "configure terminal"
_SAVE = ("end", "write")


def _range_expr(first: str, last: str) -> str:
    """锐捷的 range 写法：``GigabitEthernet 0/1-4``（连字符紧跟编号）。"""
    return f"{first}-{last.rsplit('/', 1)[-1]}"


def _if_blocks(port_list, body, indent=" ") -> list[str]:
    if not port_list:
        return []
    ok, first, last = ports.sequential(port_list)
    if ok:
        lines = [f"interface range {_range_expr(first, last)}"]
        lines += [indent + ln for ln in body]
        lines.append(" exit")
        return lines
    out: list[str] = []
    for name in port_list:
        out.append(f"interface {name}")
        out += [indent + ln for ln in body]
        out.append(" exit")
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
            lines.append(f" name {names[v]}")
        lines.append("exit")
    b.block("创建 VLAN", lines)

    if port_list:
        if p["port_type"] == "trunk":
            allowed = (p.get("allowed", "") or "").strip() or "all"
            body = ["switchport mode trunk"]
            if allowed.lower() == "all":
                body.append("switchport trunk allowed vlan all")
                b.warn("Trunk 放通全部 VLAN 会扩大广播域，建议改为明确的 VLAN 列表。")
            else:
                body.append(f"switchport trunk allowed vlan add "
                            f"{B.compact_vlans(B.parse_vlans(allowed), ',', '-')}")
            pvid = str(p.get("pvid", "")).strip()
            if pvid and pvid != "1":
                body.append(f"switchport trunk native vlan {pvid}")
            b.block("配置 Trunk 端口", _if_blocks(port_list, body),
                    comment="锐捷追加放通 VLAN 必须带 add，否则会覆盖已有列表。")
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
                        _if_blocks(group, ["switchport mode access", f"switchport access vlan {vid}"]))

    b.block("保存配置", _SAVE)
    return b.done()


def _lag(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group"), "聚合组号")
    members = ports.expand(p.get("ports", ""), VENDOR.key)
    mode = "active" if p["mode"] == "lacp" else "on"

    lines = [_ENTER]
    ok, first, last = ports.sequential(members)
    if ok:
        lines.append(f"interface range {_range_expr(first, last)}")
    else:
        lines.append("interface " + ", ".join(members))
    lines += [f" port-group {group} mode {mode}", " exit"]
    b.block("成员端口加入聚合组", lines,
            comment="锐捷的聚合口叫 AggregatePort（AP），成员口用 port-group 加入。"
                    "LACP 用 mode active，静态聚合用 mode on。")
    b.warn("若设备不支持 port-group mode 参数，请去掉 mode 部分直接写 port-group 组号。")

    body = []
    link_type = p.get("link_type", "trunk")
    if link_type == "trunk":
        allowed = (p.get("allowed", "") or "").strip() or "all"
        body = ["switchport mode trunk"]
        if allowed.lower() != "all":
            body.append(f"switchport trunk allowed vlan add "
                        f"{B.compact_vlans(B.parse_vlans(allowed), ',', '-')}")
        pvid = str(p.get("pvid", "")).strip()
        if pvid and pvid != "1":
            body.append(f"switchport trunk native vlan {pvid}")
    elif link_type == "access":
        body = ["switchport mode access"]
    b.block("配置聚合口", [_ENTER, f"interface AggregatePort {group}"]
            + [" " + ln for ln in body] + [" exit"],
            comment="聚合口的 VLAN 配置必须与成员口一致，否则无法正常转发。")

    b.block("保存配置", _SAVE)
    return b.done()


def _stp(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    mode = p["mode"]
    mode_name = {"mstp": "mstp", "rstp": "rstp", "stp": "stp"}[mode]

    b.block("启用生成树并设置模式",
            [_ENTER, "spanning-tree", f"spanning-tree mode {mode_name}"])

    if mode == "mstp":
        instances = B.parse_pairs(p.get("instances", ""))
        conf = [_ENTER, "spanning-tree mst configuration",
                f" name {B.ensure(p.get('region'), 'MST 域名')}",
                f" revision {p.get('revision') or 1}"]
        for inst, vlan_spec in instances.items():
            vlans = B.parse_vlans(vlan_spec)
            conf.append(f" instance {inst} vlan {B.compact_vlans(vlans, ',', '-')}")
        conf.append("exit")
        b.block("MST 域配置", conf,
                comment="锐捷的修订级别字段用 revision，且退出配置模式后立即生效。")
        if not instances:
            b.warn("未填写实例与 VLAN 映射，所有 VLAN 默认映射到实例 0。")

    role = p.get("role", "none")
    if role in ("primary", "secondary"):
        inst_list = list(B.parse_pairs(p.get("instances", "")).keys()) or [0]
        prio = 4096 if role == "primary" else 8192
        lines = [_ENTER]
        for inst in inst_list:
            lines.append(f"spanning-tree mst {inst} priority {prio}")
        b.block("指定根桥角色", lines,
                comment=f"锐捷用优先级间接指定根桥，主根 {prio}、备根 8192，"
                        "数值越小越优先，步长 4096。")

    edge = ports.expand(p.get("edge_ports", ""), VENDOR.key)
    if edge:
        b.block("接入口设为边缘端口",
                [_ENTER] + _if_blocks(edge, ["spanning-tree portfast"]),
                comment="配合 spanning-tree bpduguard enable 可防止接入口被串入交换机。")

    b.block("保存配置", _SAVE)
    return b.done()


# --------------------------------------------------------------------------
# 三层
# --------------------------------------------------------------------------

def _dhcp(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlan_id = B.ensure(p.get("vlan_id"), "三层接口 VLAN")
    ip = B.ensure(p.get("ip"), "接口 IP")
    mask = int(p.get("mask") or 24)
    dotted = B.prefix_to_dotted(mask)

    if p["mode"] == "relay":
        servers = B.parse_ip_list(p.get("relay", ""))
        if not servers:
            raise ValueError("中继模式下必须填写中继目标服务器地址。")
        lines = [_ENTER, f"interface Vlan {vlan_id}", f" ip address {ip} {dotted}"]
        lines += [f" ip helper-address {s}" for s in servers]
        lines.append(" exit")
        b.block("配置 DHCP 中继", lines)
        b.block("保存配置", _SAVE)
        return b.done()

    pool = B.ensure(p.get("pool"), "地址池名称")
    network = B.ensure(p.get("network"), "地址池网段")
    excludes = B.parse_ip_list(p.get("exclude", ""))
    lines = [_ENTER, "service dhcp"]
    if excludes:
        lines.append(f"ip dhcp excluded-address {excludes[0]}"
                     + (f" {excludes[-1]}" if len(excludes) > 1 else ""))
    lines += [f"ip dhcp pool {pool}",
              f" network {network} {dotted}"]
    if p.get("gateway"):
        lines.append(f" default-router {p['gateway'].strip()}")
    dns = B.parse_ip_list(p.get("dns", ""))
    if dns:
        lines.append(f" dns-server {' '.join(dns)}")
    lines += [" exit",
              f"interface Vlan {vlan_id}",
              f" ip address {ip} {dotted}",
              " exit"]
    b.block("配置 DHCP 服务器", lines,
            comment="锐捷用的是 service dhcp 开启服务，接口写法是 Vlan 10（带空格）。")
    b.block("保存配置", _SAVE)
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
        cmd = f"ip route {r.net} {B.prefix_to_dotted(r.prefix)} {r.nexthop}"
        if r.pref:
            cmd += f" {r.pref}"
        lines.append(cmd)
    b.block("配置静态路由", lines,
            comment="最后一段是管理距离，默认 1，数值越大越不优先，可用于浮动路由。")
    lines = [_ENTER, "show ip route static"]
    b.block("下发后核对", lines)
    b.block("保存配置", _SAVE)
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
        acl_id = "100" if advanced else "10"
        b.note(f"未填写 ACL 编号，已默认使用 {acl_id}。")

    keyword = "extended" if advanced else "standard"
    lines = [_ENTER, f"ip access-list {keyword} {acl_id}"]
    for i, rule in enumerate(rules, start=1):
        parts = [f" {i * 10} {rule.action}"]
        if advanced:
            parts.append(rule.proto)
            if rule.src.lower() != "any":
                net, plen = B.parse_net(rule.src)
                parts.append(f"{net} {B.prefix_to_wildcard(plen)}")
            else:
                parts.append("any")
            if rule.dst.lower() != "any":
                net, plen = B.parse_net(rule.dst)
                parts.append(f"{net} {B.prefix_to_wildcard(plen)}")
            else:
                parts.append("any")
            if rule.op and rule.port:
                parts.append(f"{rule.op} {rule.port}")
        else:
            # 标准 ACL 只匹配源地址，不写协议关键字
            if rule.src.lower() == "any":
                parts.append("any")
            else:
                net, plen = B.parse_net(rule.src)
                parts.append(f"{net} {B.prefix_to_wildcard(plen)}")
        lines.append(" ".join(parts))
    lines.append(" exit")
    b.block("定义 ACL", lines,
            comment="锐捷扩展 ACL 编号为 100-199，标准 ACL 为 1-99，命名的写法则用名称。")

    if not advanced and any(r.dst.lower() != "any" or r.op for r in rules):
        b.warn("当前是标准 ACL（只能匹配源地址），规则里的目的地址与端口字段已按语法忽略；"
               "需要按五元组过滤请改用 100-199 的扩展 ACL。")

    if any(r.action == "permit" for r in rules) and not any(r.action == "deny" for r in rules):
        b.warn("ACL 末尾存在隐式拒绝，若不希望误拦，请显式补一条 permit 规则。")

    apply_ports = ports.expand(p.get("apply_port", ""), VENDOR.key)
    if apply_ports:
        direction = "in" if p.get("direction", "inbound") == "inbound" else "out"
        b.block("应用到端口",
                [_ENTER] + _if_blocks(apply_ports, [f"ip access-group {acl_id} {direction}"]))
    b.block("保存配置", _SAVE)
    return b.done()


def _mirror(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group"), "镜像组号")
    monitor = ports.expand(p.get("monitor", ""), VENDOR.key)
    if len(monitor) != 1:
        raise ValueError("监控端口只能填一个。")
    direction = {"both": "both", "inbound": "rx", "outbound": "tx"}.get(
        p.get("direction", "both"), "both")

    vlan = (p.get("vlan") or "").strip()
    if vlan:
        src_line = f"monitor session {group} source vlan {vlan} {direction}"
    else:
        source = ports.expand(p.get("source", ""), VENDOR.key)
        if not source:
            raise ValueError("必须指定被镜像端口。")
        ok, first, last = ports.sequential(source)
        if ok:
            src_line = (f"monitor session {group} source interface "
                        f"{_range_expr(first, last)} {direction}")
        else:
            src_line = f"monitor session {group} source interface {','.join(source)} {direction}"

    b.block("配置镜像会话", [
        _ENTER,
        src_line,
        f"monitor session {group} destination interface {monitor[0]}",
        " exit",
    ], comment="锐捷的镜像命令与思科一致，方向用 rx / tx / both。")
    b.block("保存配置", _SAVE)
    return b.done()


def _mgmt(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    sysname = B.ensure(p.get("sysname"), "设备名")
    vlan_id = B.ensure(p.get("mgmt_vlan"), "管理 VLAN")
    mgmt_ip = B.ensure(p.get("mgmt_ip"), "管理地址")
    mask = int(p.get("mask") or 24)
    dotted = B.prefix_to_dotted(mask)
    user = B.ensure(p.get("user"), "管理员账号")
    password = B.ensure(p.get("password"), "密码")
    level = str(p.get("level") or 15)
    domain = (p.get("domain") or "scg.local").strip()

    lines = [_ENTER, f"hostname {sysname}",
             f"vlan {vlan_id}", " exit",
             f"interface Vlan {vlan_id}",
             f" ip address {mgmt_ip} {dotted}",
             " exit"]
    if p.get("gateway"):
        lines.append(f"ip route 0.0.0.0 0.0.0.0 {p['gateway'].strip()}")
    b.block("设备名与管理地址", lines)

    lines = [_ENTER,
             f"username {user} privilege {level} password {password}",
             f"ip domain-name {domain}",
             "crypto key generate rsa",
             "ip ssh version 2",
             "",
             "line vty 0 4",
             " transport input ssh",
             " login local",
             " exec-timeout 10 0",
             " exit",
             "enable service ssh-server",
             "no enable service telnet-server"]
    b.block("管理员账号与 SSH", lines,
            comment="锐捷默认关闭 SSH 服务端，必须显式 enable service ssh-server，"
                    "否则客户端一定连不上；生成 RSA 密钥前必须先配好 hostname 与 ip domain-name。")
    b.warn("脚本中的密码是明文，交付后请立即更换。")

    if p.get("ntp"):
        b.block("NTP 时间同步", [_ENTER, f"ntp server {p['ntp'].strip()}"])

    ver = p.get("snmp_ver", "off")
    if ver == "v2c" and p.get("snmp_community"):
        comm = p["snmp_community"].strip()
        lines = [_ENTER, f"snmp-server community {comm} ro"]
        if p.get("snmp_trap"):
            lines.append(f"snmp-server host {p['snmp_trap'].strip()} version 2c {comm}")
            lines.append("snmp-server enable traps")
        b.block("SNMP（v2c）", lines)
        b.warn("SNMP v2c 团体字明文传输，生产环境建议升级到 v3。")
    elif ver == "v3":
        b.block("SNMP（v3）", [
            _ENTER,
            "snmp-server group SCG-GROUP v3 priv",
            "snmp-server user scguser SCG-GROUP v3 auth sha AuthPass priv aes 128 PrivPass",
        ], comment="请把 AuthPass / PrivPass 换成实际密码。")

    b.block("保存配置", _SAVE)
    return b.done()


# --------------------------------------------------------------------------
# 高可用
# --------------------------------------------------------------------------

def _stacking(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    member = B.ensure(p.get("member_id"), "成员号")
    priority = B.ensure(p.get("priority"), "优先级")
    domain = (p.get("domain") or "1").strip()
    p1 = ports.expand(p.get("port1", ""), VENDOR.key)
    p2 = ports.expand((p.get("port2") or "").strip(), VENDOR.key)
    if not p1:
        raise ValueError("必须指定 VSL 端口 1。")

    lines = [_ENTER, f"switch virtual domain {domain}", f" switch {member}",
             f"  switch {member} priority {priority}", "  vsl-port"]
    for name in p1 + p2:
        lines.append(f"   port-member interface {name}")
    lines += ["   exit", "  exit", " exit"]
    b.block("配置 VSU 虚拟域与 VSL 链路", lines,
            comment="锐捷的堆叠叫 VSU，互联链路叫 VSL，用 switch virtual domain 组织。"
                    "两台设备分别填入各自的成员号与优先级。")

    b.block("保存并激活", [_ENTER, "write", "reload"],
            comment="部分版本需在保存后手动执行 vsl-port-configuration active 或直接重启生效，"
                    "请按本机手册确认。")

    b.warn("VSU 激活会重启设备，务必在维护窗口内操作。")
    b.note("VSL 建议使用至少两条万兆链路并做跨设备聚合，避免单条链路故障导致分裂。")
    if not p2:
        b.note("只配了一条 VSL 链路，链路故障会导致 VSU 分裂，建议配置第二条。")
    return b.done()


def _gateway(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlan_id = B.ensure(p.get("vlan_id"), "接口 VLAN")
    group = B.ensure(p.get("group"), "备份组号")
    vip = B.ensure(p.get("virtual_ip"), "虚拟网关 IP")
    iface_ip = B.ensure(p.get("interface_ip"), "本机接口 IP")
    mask = int(p.get("mask") or 24)
    dotted = B.prefix_to_dotted(mask)
    priority = B.ensure(p.get("priority"), "优先级")

    hsrp = p.get("proto") == "hsrp"
    lines = [_ENTER, f"interface Vlan {vlan_id}", f" ip address {iface_ip} {dotted}"]
    if hsrp:
        lines += [f" standby {group} ip {vip}", f" standby {group} priority {priority}"]
        if B.truthy(p.get("preempt")):
            lines.append(f" standby {group} preempt delay minimum 30")
        comment = "HSRP 为思科体系协议，锐捷仅部分型号支持，跨厂商组网请使用 VRRP。"
    else:
        lines += [f" vrrp {group} ip {vip}", f" vrrp {group} priority {priority}"]
        if B.truthy(p.get("preempt")):
            lines.append(f" vrrp {group} preempt")
        comment = "锐捷的 VRRP 语法与思科一致，能与其他厂商的 VRRP 互通。"

    track = (p.get("track") or "").strip()
    if track:
        trk = ports.expand(track, VENDOR.key)[0]
        if hsrp:
            lines.append(f" standby {group} track {trk} decrement 30")
        else:
            lines.append(f" vrrp {group} track {trk} decrement 30")
    lines.append(" exit")
    b.block("配置网关冗余", lines, comment=comment)
    b.block("保存配置", _SAVE)
    return b.done()


# --------------------------------------------------------------------------
# 第二批：二层安全
# --------------------------------------------------------------------------

def _port_isolation(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写需要隔离的端口。")

    lines = [_ENTER]
    if B.truthy(p.get("l3_isolate")):
        lines.append("protected-ports route-deny")
    lines += _if_blocks(port_list, ["switchport protected"])
    lines += ["end", "write"]
    b.block("配置保护口（端口隔离）", lines,
            comment="锐捷把端口隔离叫保护口/protected port。"
                    "protected-ports route-deny 是全局开关，开了之后保护口之间"
                    "连三层路由也不通，不能按端口单独设置。")
    b.block("核对结果", [_ENTER, "show protected-ports", "end"],
            comment="列出当前已设为保护口的接口。")

    uplink = ports.expand(p.get("uplink", ""), VENDOR.key)
    if uplink:
        b.note(f"上行口 {', '.join(uplink)} 未设为保护口，保护口与它之间可以正常通信。")
    else:
        b.warn("没有指定上行口。请确认至少有一个端口不是保护口，否则保护口无法与外部通信。")
    b.note("保护口只支持以太网物理接口与聚合口；聚合口设为保护口后，其成员口一并生效。")
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

    body = ["switchport port-security",
            f"switchport port-security maximum {max_mac}",
            f"switchport port-security violation {action}"]
    if aging:
        body.append(f"switchport port-security aging time {aging}")
    if B.truthy(p.get("sticky")):
        body.append("switchport port-security mac-address sticky")

    macs = B.parse_macs(p.get("macs", ""))
    for mac in macs:
        if vlan:
            body.append(f"switchport port-security mac-address {B.mac_dotted(mac)} vlan {vlan}")
        else:
            body.append(f"switchport port-security mac-address {B.mac_dotted(mac)}")

    b.block("配置端口安全",
            [_ENTER] + _if_blocks(port_list, body) + ["end", "write"],
            comment="锐捷的命令是 switchport port-security（不是 port-security enable）。"
                    "违例动作可取 protect / restrict / shutdown。")
    b.block("查看端口安全状态",
            [_ENTER, "show port-security", "show port-security address", "end"],
            comment="确认绑定表里是否已经出现了你想要的 MAC，以及违例计数有没有涨。")

    if macs and not vlan:
        b.note("手工绑定的 MAC 没有指定 VLAN，已按「该端口任意 VLAN 都放行」生成。")
    b.warn("SPAN 的目的端口不能配端口安全；DHCP Snooping 的信任端口上也配不了。")
    b.warn("端口安全不能与全局 IP+MAC 绑定、IP Source Guard 同时生效，"
           "配了 MAC 绑定的口会绕过端口安全检查。")
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
        suffix = f"level {value}"
    else:
        if not value.isdigit():
            raise ValueError("pps 阈值必须是整数。")
        suffix = f"pps {value}"

    body: list[str] = []
    if B.truthy(p.get("bcast")):
        body.append(f"storm-control broadcast {suffix}")
    if B.truthy(p.get("mcast")):
        body.append(f"storm-control multicast {suffix}")
    if B.truthy(p.get("ucast")):
        body.append(f"storm-control unicast {suffix}")
    if not body:
        raise ValueError("广播 / 组播 / 未知单播至少要勾一项。")

    b.block("配置风暴抑制",
            [_ENTER] + _if_blocks(port_list, body) + ["end", "write"],
            comment="level 后面跟数字表示带宽百分比；按包速率限速用 pps <值>。"
                    "web 界面不支持 pps 模式，只能用命令行配。")
    b.block("核对结果", [_ENTER, "show storm-control", "end"],
            comment="确认阈值已经按填写值生效。")
    b.note("锐捷交换机所有端口出厂默认就配了 10% 的广播/组播/单播抑制，"
           "没有特殊需求可以不动。")
    return b.done()


def _loop_guard(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    interval = B.ensure(p.get("interval") or "10", "检测周期")
    action = p.get("action", "shutdown")

    b.block("全局开启环路检测",
            [_ENTER, "loopback-detection enable",
             f"loopback-detection interval {interval}", "end", "write"],
            comment="锐捷的环路检测是全局开关，不能只对某几个端口开或关，"
                    "开启后所有 up 的端口都会发检测报文。"
                    "默认控制方式是「断开连接（shutdown）」，自动恢复时间默认 60 秒。")
    if action == "block":
        b.warn("锐捷没有与 H3C / 华为「阻塞端口」完全对应的档位，"
               "检测到环路后默认就是把端口断掉，如需其他动作请查本机手册。")
    elif action == "log":
        b.warn("锐捷默认检测到环路就会断开端口，本工具没有找到「只记日志不动端口」的稳定写法，"
               "请用 loopback-detection ? 确认后再调。")

    if B.truthy(p.get("bpdu_guard")):
        edge = ports.expand(p.get("edge_ports", ""), VENDOR.key)
        if edge:
            b.block("边缘端口 + BPDU 保护",
                    [_ENTER] + _if_blocks(edge, ["spanning-tree portfast",
                                                 "spanning-tree bpduguard enable"])
                    + ["end"],
                    comment="边缘端口一旦收到 BPDU，说明下面接的不是终端而是交换机，"
                            "设备会直接把该端口 err-disable。")
        else:
            b.warn("开了 BPDU 保护但没有指定边缘端口，请在用户接入口上补配置。")
        b.block("BPDU 保护触发后的自动恢复", [
            _ENTER,
            "errdisable recovery cause bpduguard",
            f"errdisable recovery interval {int(interval) * 30 if interval.isdigit() else 300}",
            "end",
        ], comment="err-disable 的端口默认不会自己恢复，配了这两条才会定时重试。"
                   "部分版本没有 errdisable 命令，下发前用 ? 确认。")

    b.block("核对结果", [_ENTER, "show loopback-detection", "end"],
            comment="查看全局环路检测状态与检测到的环路端口。")
    b.note("环路检测和生成树不要同时乱开：跑了 STP 的二层网络里，环路应由 STP 处理，"
           "环路检测更适合兜底傻瓜交换机造成的环路。")
    return b.done()


# --------------------------------------------------------------------------
# 第二批：接入安全
# --------------------------------------------------------------------------

def _dhcp_snooping(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))
    vlan_arg = B.compact_vlans(vlans)

    lines = [_ENTER, "ip dhcp snooping", f"ip dhcp snooping vlan {vlan_arg}"]
    if not B.truthy(p.get("check_mac")):
        lines.append("no ip dhcp snooping verify mac-address")
    lines.append("end")
    b.block("开启 DHCP Snooping", lines,
            comment="ip dhcp snooping 是总开关，再按 VLAN 打开。"
                    "默认会校验请求报文里的客户端 MAC 与源 MAC 是否一致，"
                    "本工具按勾选情况生成。")

    trust = ports.expand(p.get("trust_ports", ""), VENDOR.key)
    if trust:
        b.block("指定信任端口",
                [_ENTER] + _if_blocks(trust, ["ip dhcp snooping trust"]) + ["end"],
                comment="只有信任端口上收到的 DHCP 应答才会被转发，其余一律丢弃。")
    else:
        b.warn("没有指定信任端口，全网 DHCP 应答都会被丢弃，客户端拿不到地址。")

    b.block("保存配置", _SAVE)
    b.block("查看绑定表", [_ENTER, "show ip dhcp snooping binding", "end"],
            comment="绑定表是动态 ARP 检测和 IP 源防护的判断依据。")
    b.note("锐捷与思科的 DHCP Snooping 命令基本一致，这也是 RGOS 与 IOS 同源的体现。")
    return b.done()


def _arp_guard(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))
    vlan_arg = B.compact_vlans(vlans)

    lines = [_ENTER, "ip dhcp snooping", f"ip dhcp snooping vlan {vlan_arg}",
             f"ip arp inspection vlan {vlan_arg}"]
    if B.truthy(p.get("validate")):
        lines.append("ip arp inspection validate src-mac dst-mac ip")
    lines.append("end")
    b.block("开启动态 ARP 检测（DAI）", lines,
            comment="DAI 靠 DHCP Snooping 绑定表判断报文是否合法，"
                    "所以要先开 DHCP Snooping 并指定信任端口。")

    trust = ports.expand(p.get("trust_ports", ""), VENDOR.key)
    if trust:
        b.block("指定信任端口",
                [_ENTER] + _if_blocks(trust, ["ip dhcp snooping trust",
                                              "ip arp inspection trust"]) + ["end"],
                comment="上行口必须同时设为两者的信任口，否则来自网关的正常 ARP 会被丢弃。")
    else:
        b.warn("没有指定信任端口，上行链路的 ARP 会被误拦，请务必设置。")

    if B.truthy(p.get("ip_source_guard")):
        b.note("IP 源防护的命令随 RGOS 版本差异较大（ip verify source / ip source-guard），"
               "本工具没有生成，请用 ? 确认后再配。")
        b.warn("IP 源防护依赖绑定表。终端若用静态 IP，必须先建静态绑定，否则它会连不上网。")

    b.block("保存配置", _SAVE)
    b.block("核对结果", [_ENTER, "show ip arp inspection", "end"],
            comment="确认生效的 VLAN 列表与信任端口是否正确。")
    return b.done()


# --------------------------------------------------------------------------
# 第二批：三层 / 组播
# --------------------------------------------------------------------------

def _igmp_snooping(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    lines = [_ENTER, "ip igmp snooping"]
    for v in vlans:
        lines.append(f"ip igmp snooping vlan {v}")
    lines.append("end")
    b.block("开启 IGMP Snooping", lines,
            comment="全局一条打开，再逐 VLAN 打开。")

    if B.truthy(p.get("querier")):
        ip = B.ensure(p.get("querier_ip"), "查询器源地址")
        lines = [_ENTER, "ip igmp snooping querier"]
        for v in vlans:
            lines.append(f"ip igmp snooping vlan {v} querier")
        lines.append("end")
        b.block("开启 IGMP 查询器", lines,
                comment="VLAN 内没有三层组播设备时，由交换机自己发查询报文维持组播表项。")
        b.note(f"要把查询器地址固定成 {ip}，可参考本机手册在 VLAN 接口下配置，"
               f"该命令随版本差异较大，本工具没有生成。")

    router_ports = ports.expand(p.get("router_ports", ""), VENDOR.key)
    if router_ports:
        lines = [_ENTER]
        for v in vlans:
            for name in router_ports:
                lines.append(f"ip igmp snooping vlan {v} mrouter interface {name}")
        lines.append("end")
        b.block("静态指定路由器端口", lines,
                comment="组播流量从这些口进来。不指定的话靠动态学习，上行不稳时表项容易丢。")

    if B.truthy(p.get("fast_leave")):
        lines = [_ENTER]
        for v in vlans:
            lines.append(f"ip igmp snooping vlan {v} immediate-leave")
        lines.append("end")
        b.block("开启快速离开", lines,
                comment="收到离开报文立即停止转发，一个口只接一台终端时才建议开。")

    b.block("保存配置", _SAVE)
    b.warn("同一个 VLAN 里只应存在一个 IGMP 查询器，多台设备都开容易互相干扰。")
    return b.done()


def _ospf(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    process = B.ensure(p.get("process") or "1", "进程号")
    router_id = B.ensure(p.get("router_id"), "Router ID")
    nets = B.parse_routes_area(p.get("networks", ""))
    if not nets:
        raise ValueError("请至少宣告一个网段。")

    lines = [_ENTER, "ip routing", f"router ospf {process}",
             f" router-id {router_id}"]
    for net, plen, area in nets:
        lines.append(f" network {net} {B.prefix_to_wildcard(plen)} area {area}")
    areas: list[str] = []
    for _, _, area in nets:
        if area not in areas:
            areas.append(area)
    if p.get("auth") == "md5":
        for area in areas:
            lines.append(f" area {area} authentication message-digest")
    lines += [" end", "write"]
    b.block("启用 OSPF 并宣告网段", lines,
            comment="锐捷的 OSPF 语法与思科基本一致，network 一条带上区域号，用反掩码。"
                    "三层交换机别忘了 ip routing。")

    passive = B.split_ifaces(p.get("passive"))
    if passive:
        lines = [_ENTER, f"router ospf {process}"]
        lines += [f" passive-interface {name}" for name in passive]
        lines.append("end")
        b.block("设置被动接口", lines,
                comment="被动接口不发 Hello，但网段照样宣告出去，适合接终端的接口。")

    if p.get("auth") == "md5":
        key = B.ensure(p.get("auth_key"), "验证密钥")
        b.block("接口上配置 MD5 密钥", [
            "! 区域开了 MD5 验证后，还要在每个跑 OSPF 的接口上配密钥，",
            "! 命令形如（在 VLAN 接口视图下）：",
            f"!   ip ospf authentication message-digest",
            f"!   ip ospf message-digest-key 1 md5 {key}",
            "! 密钥必须两端一致。本工具不知道你的三层接口清单，所以只给模板。",
        ], comment="这一段是注释，不会下发；请按实际三层接口补配。")
        b.warn(f"MD5 密钥 {key} 是明文，下发前请确认终端没被别人看到。")

    b.block("核对邻居与路由",
            [_ENTER, "show ip ospf neighbor", "show ip route ospf", "end"],
            comment="先看邻居是不是 FULL，再看 OSPF 路由有没有进表。")
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

    name = f"SCG-RATE-{cir}K"
    police = f"police cir {int(cir) * 1000}"
    if cbs:
        if not cbs.isdigit():
            raise ValueError("突发尺寸必须是整数（单位字节）。")
        police += f" cbs {cbs}"
    else:
        police += f" cbs {max(64000, int(cir) * 1000 // 8 // 5)}"

    lines = [_ENTER,
             f"policy-map {name}",
             " class class-default",
             f"  {police}",
             "  exit",
             " exit",
             "end"]
    b.block("定义限速策略", lines,
            comment=f"police cir 单位是 bps，所以 {cir} kbps 换算成 {int(cir) * 1000} bps。"
                    "没填 CBS 时，工具按「CIR × 200ms」估算了一个经验值，"
                    "请按现场情况调整。")

    svc = f"service-policy {direction} {name}"
    b.block("在端口上应用策略",
            [_ENTER] + _if_blocks(port_list, [svc]) + ["end", "write"],
            comment="入方向限的是用户上传，出方向限的是用户下载。")
    b.block("核对限速结果",
            [_ENTER, f"show policy-map interface | include {name}", "end"],
            comment="看有没有命中计数，计数为 0 说明策略没生效。")
    b.note("锐捷也可以用 rate-limit input <bps> <突发> 直接在接口下限速，"
           "不同版本参数个数不一样，本工具没生成，需要时请用 ? 确认。")
    return b.done()


def _inspect(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    if not any(B.truthy(p.get(k)) for k in
               ("basic", "interface", "l2", "l3", "security", "hardware", "logs")):
        raise ValueError("至少勾选一类巡检内容。")

    if B.truthy(p.get("basic")):
        b.block("设备信息与资源", [
            "show version", "show version slots", "show clock",
            "show cpu", "show memory", "show boot",
            "show environment", "show fan", "show power",
        ], comment="开局先留一份基线，出问题时拿它对比。")

    if B.truthy(p.get("interface")):
        b.block("接口与流量", [
            "show interfaces status", "show interfaces",
            "show interfaces counters errors", "show interfaces counters summary",
            "show interfaces counters rate", "show interfaces transceiver",
        ], comment="看错包、丢包和速率，是判断链路质量最快的一组命令。")

    if B.truthy(p.get("l2")):
        b.block("二层转发表", [
            "show vlan", "show vlan brief",
            "show mac-address-table", "show mac-address-table count",
            "show interfaces aggregateport", "show spanning-tree",
            "show spanning-tree summary", "show lldp neighbors",
            "show lldp neighbors detail",
        ], comment="MAC 表在两三个口之间来回跳，基本就是环路或 MAC 漂移。")

    if B.truthy(p.get("l3")):
        b.block("三层与地址", [
            "show ip route", "show ip route summary", "show arp",
            "show ip interface brief", "show ip dhcp binding",
            "show vrrp brief", "show ip ospf neighbor", "show ip ospf route",
        ], comment="先看路由表里有没有你要的那条，再看 ARP 学没学到对端。")

    if B.truthy(p.get("security")):
        b.block("安全与接入", [
            "show ip dhcp snooping", "show ip dhcp snooping binding",
            "show ip arp inspection", "show ip arp inspection statistics",
            "show port-security", "show port-security address",
            "show dot1x", "show acl", "show storm-control",
        ], comment="命中计数长期为 0 的规则，往往说明它根本没生效。")

    if B.truthy(p.get("hardware")):
        b.block("硬件与光模块", [
            "show interfaces transceiver", "show interfaces transceiver diagnosis",
            "show poe interfaces", "show poe power", "show power-supply",
        ], comment="光模块收发功率是排查「链路 up 但丢包」的关键数据。")

    if B.truthy(p.get("logs")):
        b.block("日志与告警", [
            "show logging", "show logging buffer", "show logging server",
            "show alarm", "show version | include uptime",
        ], comment="日志缓冲多少能看出设备最近有没有被反复重启。")

    if B.truthy(p.get("export")):
        b.block("一键收集诊断信息", ["show tech-support"],
                comment="这个命令输出非常大、耗时长，"
                        "提交给技术支持时非常有用，但要先确保终端能完整保存输出。")

    b.block("保存配置", _SAVE)
    b.note("以上全部是查询命令，不会改动设备配置，可以放心执行。")
    b.warn("个别 show 命令随 RGOS 版本不同（例如聚合口状态、光模块信息），"
           "下发前请用 ? 确认本机写法。")
    return b.done()


def _backup(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    server = B.ensure(p.get("server"), "服务器地址")
    filename = B.ensure(p.get("filename"), "文件名")
    proto = p.get("proto", "tftp")
    action = p.get("action", "backup")
    user = str(p.get("user") or "").strip()
    password = str(p.get("password") or "").strip()

    if proto == "ftp":
        if not user or not password:
            raise ValueError("FTP 方式需要填写用户名和密码。")
        cred = f"{user}:{password}@"
        b.warn("FTP 密码会明文出现在命令里，建议改用 TFTP，或备份完立刻更换密码。")
    else:
        cred = ""
    base = f"{proto}://{cred}{server}/{filename}"

    if B.truthy(p.get("save_first")):
        b.block("先保存当前配置", [_ENTER, "write memory", "end"],
                comment="不先保存的话，备份走的是上一次保存过的启动配置。")

    if action == "backup":
        b.block("备份配置到服务器",
                [_ENTER, f"copy running-config {base}", "end",
                 "! 上面一条把当前运行配置传到服务器；",
                 "! 想备份启动配置就换成：copy startup-config " + base],
                comment="copy 会先问一句确认，回车即可。")
        b.block("核对结果", [_ENTER, "show startup-config", "end"],
                comment="对比一下启动配置和你以为的一致。")
    else:
        b.warn("把文件覆盖 startup-config 后重启会替换全部配置，"
               "导入 running-config 则是合并、不会删掉已有配置——两种效果差别很大。")
        b.block("从服务器恢复配置", [
            _ENTER,
            f"copy {base} running-config",
            "! 上面这条是「合并」进当前运行配置，不会删除已有配置；",
            f"! 要整体替换启动配置则用：copy {base} startup-config",
            "end",
        ], comment="导入后建议先 write memory 保存，再找维护窗口重启验证。")

    b.note(f"服务器地址 {server}，文件名 {filename}，传输方式 {proto.upper()}。")
    return b.done()


def _syslog(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    level = p.get("level", "informational")
    buffer_size = str(p.get("buffer") or "512").strip()
    server = str(p.get("server") or "").strip()
    ntp = str(p.get("ntp") or "").strip()
    zone = B.parse_timezone(p.get("timezone"))

    lines = [_ENTER, "logging on", f"logging buffered {buffer_size}"]
    if B.truthy(p.get("timestamps")):
        lines.append("service timestamps log datetime msec localtime show-timezone")
    if zone:
        lines.append(B.cisco_timezone(zone))
    if B.truthy(p.get("console")):
        lines.append("logging console")
    lines.append("end")
    b.block("配置本地日志", lines,
            comment="service timestamps ... localtime 让日志带本地时间，"
                    "没有时间戳的日志在排障时几乎用不上。"
                    "锐捷默认不往 console 打日志，要开就加 logging console。")

    if server:
        b.block("配置日志服务器", [
            _ENTER, f"logging server {server}",
            f"logging trap {level}",
            "end",
        ], comment=f"只把 {level} 及以上级别的日志送到服务器，避免刷屏。"
                   "部分 RGOS 版本的写法是 logging host，下发前用 ? 确认。")

    if ntp:
        b.block("NTP 时间同步", [
            _ENTER, f"ntp server {ntp}", "end",
        ], comment="时钟不准，日志的先后顺序就不可信，NTP 要和日志一起配。")

    b.block("保存配置", _SAVE)
    b.block("验证日志是否送达",
            [_ENTER, "show logging", "show clock", "end"],
            comment="配完在设备上制造一条日志（比如插拔一个端口），"
                    "去服务器上确认收到了。")
    return b.done()


# --------------------------------------------------------------------------
# 第三批：动态路由与 MPLS VPN
# --------------------------------------------------------------------------

_PHY_PREFIX = ("GigabitEthernet", "TenGigabitEthernet", "FastEthernet",
               "FortyGigabitEthernet", "HundredGigabitEthernet", "Ethernet")


def _l3_ifaces(p, vendor_key) -> list[str]:
    out: list[str] = []
    for v in B.split_ifaces(p.get("svlan")):
        out.append(B.l3_iface(vendor_key, v))
    out.extend(B.resolve_ifaces(p.get("ports"), vendor_key))
    return out


def _l3_blocks(b, port_list, body, comment="") -> None:
    """锐捷的三层接口：物理口必须先 no switchport 才能配 IP。"""
    physical, others = [], []
    for name in port_list:
        (physical if name.startswith(_PHY_PREFIX) else others).append(name)
    if others:
        b.block("三层接口配置", [_ENTER] + _if_blocks(others, body) + ["end"], comment)
    if physical:
        b.block("三层物理口配置（含 no switchport）",
                [_ENTER] + _if_blocks(physical, ["no switchport"] + body) + ["end"],
                comment="no switchport 会把端口从二层切成三层，"
                        "端口上原有的 VLAN、Trunk 配置会被清掉，执行前请确认。")
        b.warn("no switchport 会清空该端口原有的二层配置（VLAN、Trunk 等），"
               "在生产设备上执行前务必确认这个口本来就该做三层口。")


def _isis(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    net = B.ensure(p.get("net"), "NET 地址")
    level = p.get("level", "level-2")
    is_type = {"level-1": "level-1", "level-1-2": "level-1-2",
               "level-2": "level-2-only"}.get(level, "level-2-only")

    b.block("启用 IS-IS 并配置 NET", [
        _ENTER, "ip routing", "router isis",
        f" net {net}", f" is-type {is_type}",
        f" metric-style {p.get('cost_style', 'wide')}",
        " exit", "end",
    ], comment="锐捷与思科同源，层级关键字是 level-2-only，度量风格是 metric-style。"
               "部分 RGOS 版本的 metric-style 命令名不同，下发前用 ? 确认。")

    if B.truthy(p.get("auth")):
        key = B.ensure(p.get("auth_key"), "验证密钥")
        b.block("配置验证钥匙串", [
            _ENTER, "key chain ISIS-AUTH", " key 1",
            f"  key-string {key}", "  exit", " exit", "end",
        ], comment="锐捷的 IS-IS 验证走钥匙串，与思科一致。")

    ifaces = _l3_ifaces(p, VENDOR.key)
    if ifaces:
        body = ["ip router isis"]
        if p.get("circuit_level", "follow") != "follow":
            lvl = p["circuit_level"]
            body.append("isis circuit-type " + ("level-2-only" if lvl == "level-2" else lvl))
        if B.truthy(p.get("auth")):
            body += ["isis authentication mode md5",
                     "isis authentication key-chain ISIS-AUTH"]
        _l3_blocks(b, ifaces, body,
                   comment="接口下用 ip router isis 使能。")
    else:
        b.warn("没有填三层接口，IS-IS 起来了也建不了邻接。")

    if B.truthy(p.get("import_direct")):
        b.block("引入直连路由",
                [_ENTER, "router isis", " redistribute connected", " exit", "end"],
                comment="把直连网段发布进 IS-IS，一般都要开。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "show isis neighbors", "show ip route isis", "end"],
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

    lines = [_ENTER, "ip routing", f"router bgp {local_as}",
             f" bgp router-id {router_id}", " bgp log-neighbor-changes"]
    for peer in peers:
        lines.append(f" neighbor {peer.ip} remote-as {peer.asn}")
        if peer.desc:
            lines.append(f" neighbor {peer.ip} description {peer.desc}")
        if B.truthy(p.get("md5")):
            key = B.ensure(p.get("md5_key"), "MD5 密钥")
            lines.append(f" neighbor {peer.ip} password {key}")
        ka = str(p.get("keepalive") or "").split()
        if len(ka) == 2 and all(x.isdigit() for x in ka):
            lines.append(f" neighbor {peer.ip} timers {ka[0]} {ka[1]}")
    for net, plen in nets:
        lines.append(f" network {net} mask {B.prefix_to_dotted(plen)}")
    if B.truthy(p.get("import_direct")):
        lines.append(" redistribute connected")
    lines += [" exit", "end"]
    b.block("配置 BGP 与邻居", lines,
            comment="锐捷的 BGP 语法与思科基本一致，邻居命令是 neighbor。"
                    "三层交换机别忘了 ip routing。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "show bgp summary", "show bgp ipv4 unicast", "end"],
            comment="State/PfxRcd 那一列出现数字就是 Established。")
    if B.truthy(p.get("md5")):
        b.warn("TCP MD5 密钥在脚本里是明文，两端必须一致，不一致会一直卡在 Connect。")
    b.note("IBGP 建议用 Loopback 建邻居，并配 neighbor <地址> update-source Loopback 0。")
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

    b.block("先建好 Loopback", [
        _ENTER, f"interface {loop}",
        f" ip address {lsr} 255.255.255.255", " exit", "end",
    ], comment="LSR ID 用 Loopback 地址最稳。锐捷的环回口写法是 Loopback 0，"
               "接口名和编号之间有空格。")

    b.block("开启 MPLS 与 LDP", [
        _ENTER, "mpls ip", "mpls router ldp",
        f" ldp router-id interface {loop} force", " exit",
        f"interface {core_if}",
        f" ip address {core_ip} {B.prefix_to_dotted(core_mask)}",
        " mpls ip", " label-switch", " exit", "end",
    ], comment="锐捷的写法和其他三家都不一样：全局是 mpls ip 开转发，"
               "mpls router ldp 进 LDP 视图再用 ldp router-id 指定 Router ID，"
               "没有 mpls lsr-id 这条命令。接口下要 mpls ip 加 label-switch 两条。")
    b.warn("锐捷部分版本的接口命令写作 label-switching（带 ing），"
           "本工具生成的是官方文档里的 label-switch，下发前请用 ? 确认。")

    b.block("创建 VRF", [
        _ENTER, f"ip vrf {vrf}",
        f" rd {rd}",
        f" route-target export {rt_out}",
        f" route-target import {rt_in}",
        " exit", "end",
    ], comment="锐捷用 ip vrf（与思科一致）。")

    b.block("把 CE 接口划入 VRF", [
        _ENTER, f"interface {ce_if}",
        f" ip vrf forwarding {vrf}",
        f" ip address {ce_ip} {B.prefix_to_dotted(ce_mask)}",
        " exit", "end",
    ], comment="注意顺序：ip vrf forwarding 会清掉接口上原有的 IP 配置，"
               "所以必须先划 VRF 再配地址，反了地址就没了。")

    b.block("MP-BGP 传递 VPN 路由", [
        _ENTER, f"router bgp {as_num}", " no bgp default ipv4-unicast",
        f" neighbor {pe_peer} remote-as {as_num}",
        f" neighbor {pe_peer} update-source {loop}",
        " address-family vpnv4 unicast",
        f"  neighbor {pe_peer} activate",
        f"  neighbor {pe_peer} send-community extended",
        "  exit-address-family",
        f" address-family ipv4 vrf {vrf}",
        f"  neighbor {ce_peer} remote-as {ce_as}",
        f"  neighbor {ce_peer} activate",
        "  redistribute connected",
        "  exit-address-family",
        " exit", "end",
    ], comment="锐捷的 VPNv4 地址族写成 address-family vpnv4 unicast，"
               "比思科多一个 unicast。")

    b.block("保存配置", _SAVE)
    b.block("分段核对", [
        _ENTER, "show mpls ldp neighbor", "show mpls ldp binding",
        "show ip vrf", "show bgp vpnv4 unicast summary",
        f"show ip route vrf {vrf}", "end",
    ], comment="按顺序查：LDP 邻居通了没、标签有没有下发、VRF 建了没、"
               "MP-BGP 邻居起来没、VPN 路由收没收到。")
    b.warn("MPLS 的前提是公网侧 IGP 已经跑通，而且两端的 Loopback 都要被宣告进 IGP，"
           "否则 LDP 会话建不起来。建议先配 OSPF 或 IS-IS 那两条场景。")
    b.warn("MPLS L3VPN 是路由器级特性。锐捷这边只有 N18K、S65 以上等高端型号支持，"
           "普通接入交换机不具备这个能力，配置前先确认型号与 license。")
    return b.done()


# --------------------------------------------------------------------------
# 第四批：接入认证 / 供电 / QoS / IPv6
# --------------------------------------------------------------------------

def _dot1x(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    ip = B.ensure(p.get("radius_ip"), "RADIUS 服务器地址")
    key = B.ensure(p.get("radius_key"), "RADIUS 共享密钥")
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写受控端口。")

    b.block("配置 RADIUS 与 AAA", [
        _ENTER, "aaa new-model",
        f"radius-server host {ip} key {key}",
        "aaa authentication dot1x default group radius",
        "aaa authorization network default group radius",
        "dot1x authentication default group radius",
        "end",
    ], comment="锐捷的 RADIUS 配置比思科简单：一条 radius-server host 就把地址和密钥都写了。"
               "dot1x authentication default group radius 指定默认走 RADIUS 认证。")
    b.warn("共享密钥在脚本里是明文，交付后请改用其他方式保存或立即更换。")
    b.note("部分 RGOS 版本的 dot1x authentication default 命令不存在，"
           "只靠 aaa authentication dot1x default group radius 也够用，"
           "下发前用 ? 确认。")

    body = ["dot1x"]
    if p.get("port_method", "macbased") == "portbased":
        body.append("dot1x port-method portbased")
    body.append(f"dot1x port-control {p.get('port_control', 'auto')}")
    if p.get("guest_vlan"):
        body.append(f"dot1x guest-vlan {p['guest_vlan']}")
    b.block("端口开启 802.1X", [_ENTER] + _if_blocks(port_list, body) + ["end"],
            comment="锐捷缺省的接入控制方式就是基于 MAC，"
                    "选 portbased 时才需要显式配 dot1x port-method portbased。")

    b.block("保存配置", _SAVE)
    b.block("核对认证状态",
            [_ENTER, "show dot1x", "show dot1x summary", "end"],
            comment="看端口在不在跑 802.1X、有没有在线用户。")
    if p.get("max_user"):
        b.note(f"单端口最大用户数填了 {p['max_user']}，"
               f"锐捷对应的命令随版本不同，本工具没有生成，请用 ? 确认。")
    b.note("锐捷、思科都默认走 EAP 中继，认证方法由 RADIUS 服务器协商。")
    return b.done()


def _poe(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写供电端口。")
    action = p.get("action", "enable")

    body: list[str] = []
    if action == "disable":
        body.append("no poe enable")
    else:
        body.append("poe enable")
        body.append(f"poe priority {p.get('priority', 'high')}")

    b.block("配置 PoE 供电", [_ENTER] + _if_blocks(port_list, body) + ["end"],
            comment="poe priority 决定整机供电不足时的取舍顺序："
                    "critical 最后断电，low 最先断电。")
    b.block("核对供电状态",
            [_ENTER, "show poe interfaces", "show poe power", "end"],
            comment="确认端口在供电、功率是多少、有没有超预算。")
    b.block("保存配置", _SAVE)
    if p.get("max_power"):
        b.note(f"单口功率上限填了 {p['max_power']} 但本工具没有生成——"
               f"锐捷的限制命令随型号差异较大，请用 ? 确认本机写法后手工补配。")
    if action == "disable":
        b.warn("关闭 PoE 会让 AP、摄像头这类设备直接断电，确认不是生产设备再执行。")
    return b.done()


def _qos_priority(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写生效端口。")
    trust = p.get("trust", "dscp")

    lines = [_ENTER, "mls qos"]
    if trust == "none":
        lines += _if_blocks(port_list, ["no mls qos trust"])
    else:
        keyword = "cos" if trust == "dot1p" else "dscp"
        lines += _if_blocks(port_list, [f"mls qos trust {keyword}"])
    lines.append("end")
    b.block("开启 QoS 并配置信任模式", lines,
            comment="锐捷与思科同源，先 mls qos 开总开关，再配信任模式。"
                    "802.1p 对应的关键字是 cos。")

    if p.get("acl_id"):
        acl = B.ensure(p.get("acl_id"), "ACL 编号")
        dscp = B.ensure(p.get("dscp") or "af11", "DSCP 值")
        direction = p.get("direction", "inbound")
        b.block("定义 class-map 与 policy-map", [
            _ENTER,
            "class-map match-any SCG-CLS",
            f" match access-group {acl}", " exit",
            "policy-map SCG-POL",
            " class SCG-CLS",
            f"  set dscp {dscp}", "  exit",
            " exit", "end",
        ], comment="class-map 挑流量，policy-map 里用 set dscp 重标记。")
        b.block("把策略应用到端口",
                [_ENTER] + _if_blocks(port_list, [f"service-policy {direction} SCG-POL"])
                + ["end"],
                comment="入方向应用在接终端的口上。")
        b.note(f"ACL {acl} 需要你先建好，可以用「ACL 访问控制」那条场景生成。")

    b.block("保存配置", _SAVE)
    b.block("核对结果",
            [_ENTER, "show mls qos interface", "show policy-map interface", "end"],
            comment="确认信任模式生效、策略有命中计数。")
    b.note("部分 RGOS 版本的信任命令写作 qos trust，下发前用 ? 确认。")
    return b.done()


def _ipv6(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlan = B.ensure(p.get("svlan"), "三层接口 VLAN")
    addr = B.ensure(p.get("address"), "IPv6 地址")
    prefix = str(p.get("prefix") or "64")
    iface = B.l3_iface(VENDOR.key, vlan)
    routes = B.parse_routes6(p.get("routes", ""))

    lines = [_ENTER, "ipv6 unicast-routing", "ipv6 cef",
             f"interface {iface}",
             " ipv6 enable",
             f" ipv6 address {addr}/{prefix}"]
    if not B.truthy(p.get("nd_ra")):
        lines.append(" ipv6 nd ra suppress")
    lines.append("end")
    b.block("开启 IPv6 并配置接口地址", lines,
            comment="锐捷和思科一样，必须先 ipv6 unicast-routing 才能转发 IPv6 报文。"
                    "默认发路由通告，不要发才用 ipv6 nd ra suppress。")

    if routes:
        lines = [_ENTER]
        for dest, nexthop in routes:
            lines.append(f"ipv6 route {dest} {nexthop}")
        lines.append("end")
        b.block("配置 IPv6 静态路由", lines,
                comment="锐捷的 IPv6 静态路由写作 ipv6 route 前缀/长度 下一跳。")

    b.block("保存配置", _SAVE)
    b.block("核对结果", [
        _ENTER, "show ipv6 interface brief", "show ipv6 route",
        "show ipv6 neighbors", "end",
    ], comment="看接口地址、路由表和邻居表三项。")
    b.note("接口会自动生成 fe80:: 开头的链路本地地址，不要禁用。")
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
