"""H3C 新华三 Comware V5 / V7 命令生成。

命令以 Comware V7 为准；与 V5 的差异写在对应区块的注释里。
"""
from __future__ import annotations

from .. import ports
from ..model import GenResult, Vendor
from . import base as B

VENDOR = Vendor(
    key="h3c", name="H3C 新华三", platform="Comware V5 / V7", accent="#3B82F6",
    port_hint="接口编号形如 GigabitEthernet1/0/1；万兆为 Ten-GigabitEthernet1/0/49；"
              "三层接口为 Vlan-interface 10。",
)

_ENTER = "system-view"
_SAVE = ("return", "save force")


def _if_blocks(port_list, body, indent=" ") -> list[str]:
    """生成接口配置块；端口连续时合并成 interface range。"""
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
            lines.append(f" name {names[v]}")
        lines.append(" quit")
    b.block("创建 VLAN", lines)

    if port_list:
        if p["port_type"] == "trunk":
            allowed = p.get("allowed", "").strip() or "all"
            if allowed.lower() == "all":
                permit = "port trunk permit vlan all"
                b.warn("Trunk 放通全部 VLAN 会把广播域铺满，若不必要请改成明确的 VLAN 列表。")
            else:
                permit = f"port trunk permit vlan {B.compact_vlans(B.parse_vlans(allowed), ' ', ' to ')}"
            body = ["port link-type trunk", permit]
            pvid = str(p.get("pvid", "")).strip()
            if pvid and pvid != "1":
                body.append(f"port trunk pvid vlan {pvid}")
            b.block("配置 Trunk 端口", _if_blocks(port_list, body))
        else:
            if len(vlans) == 1:
                mapping = {name: vlans[0] for name in port_list}
            elif len(vlans) == len(port_list):
                mapping = dict(zip(port_list, vlans))
                b.note("VLAN 数与端口数相同，已按顺序一一对应划分。")
            else:
                mapping = {name: vlans[0] for name in port_list}
                b.warn(
                    f"VLAN 有 {len(vlans)} 个、端口有 {len(port_list)} 个，无法一一对应，"
                    f"已统一划入 VLAN {vlans[0]}，请确认是否符合预期。"
                )
            by_vlan: dict[int, list[str]] = {}
            for name, vid in mapping.items():
                by_vlan.setdefault(vid, []).append(name)
            for vid, group in by_vlan.items():
                b.block(f"Access 端口划入 VLAN {vid}",
                        _if_blocks(group, ["port link-type access", f"port access vlan {vid}"]))

    b.block("保存配置", _SAVE)
    return b.done()


def _lag(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group"), "聚合组号")
    members = ports.expand(p.get("ports", ""), VENDOR.key)
    link_type = p.get("link_type", "trunk")

    if link_type == "l3":
        agg_if = f"Route-Aggregation {group}"
        body = []
    else:
        agg_if = f"Bridge-Aggregation {group}"
        body = []
        if link_type == "trunk":
            allowed = p.get("allowed", "").strip() or "all"
            body.append("port link-type trunk")
            if allowed.lower() == "all":
                body.append("port trunk permit vlan all")
            else:
                body.append(f"port trunk permit vlan {B.compact_vlans(B.parse_vlans(allowed), ' ', ' to ')}")
            pvid = str(p.get("pvid", "")).strip()
            if pvid and pvid != "1":
                body.append(f"port trunk pvid vlan {pvid}")
        else:
            body.append("port link-type access")

    if p["mode"] == "lacp":
        body.append("link-aggregation mode dynamic")

    lines = [_ENTER, f"interface {agg_if}"] + [" " + ln for ln in body] + [" quit"]
    b.block("创建聚合接口", lines,
            comment="三层聚合用 Route-Aggregation，二层聚合用 Bridge-Aggregation，注意区分。")

    member_lines = [_ENTER]
    for name in members:
        member_lines += [f"interface {name}", f" port link-aggregation group {group}", " quit"]
    b.block("成员端口加入聚合组", member_lines)

    if link_type == "trunk":
        b.block("确认聚合口放通 VLAN", [
            _ENTER,
            f"interface {agg_if}",
            " display this",
            " quit",
        ], comment="确认成员口 VLAN 配置与聚合口一致，不一致会加入失败。")

    b.block("保存配置", _SAVE)
    return b.done()


def _stp(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    mode = p["mode"]

    lines = [_ENTER, "stp global enable", f"stp mode {mode}"]
    b.block("启用并设置生成树模式", lines,
            comment="Comware V5 的启用命令是 stp enable，V7 为 stp global enable。")

    if mode == "mstp":
        instances = B.parse_pairs(p.get("instances", ""))
        conf = [_ENTER, "stp region-configuration",
                f" region-name {B.ensure(p.get('region'), 'MST 域名')}",
                f" revision-level {p.get('revision') or 0}"]
        for inst, vlan_spec in instances.items():
            vlans = B.parse_vlans(vlan_spec)
            conf.append(f" instance {inst} vlan {B.compact_vlans(vlans, ' ', ' to ')}")
        conf.append(" active region-configuration")
        conf.append(" quit")
        b.block("MSTP 域配置", conf,
                comment="同一域内所有设备的域名、修订级别、实例映射必须完全一致。")
        if not instances:
            b.warn("未填写实例与 VLAN 映射，所有 VLAN 将默认映射到实例 0。")

    role = p.get("role", "none")
    if role in ("primary", "secondary"):
        inst_list = list(B.parse_pairs(p.get("instances", "")).keys()) or [0]
        lines = [_ENTER]
        for inst in inst_list:
            lines.append(f"stp instance {inst} root {role}")
        if mode != "mstp":
            lines = [_ENTER, f"stp root {role}"]
        b.block("指定根桥角色", lines,
                comment="根桥应部署在核心层，接入层设备不要抢根。")

    edge = ports.expand(p.get("edge_ports", ""), VENDOR.key)
    if edge:
        b.block("接入口设为边缘端口",
                [_ENTER] + _if_blocks(edge, ["stp edged-port"]),
                comment="边缘端口不参与拓扑计算，插拔终端不会引发网络震荡。")

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
    iface = f"Vlan-interface {vlan_id}"

    if p["mode"] == "relay":
        servers = B.parse_ip_list(p.get("relay", ""))
        if not servers:
            raise ValueError("中继模式下必须填写中继目标服务器地址。")
        lines = [_ENTER, "dhcp enable",
                 f"interface {iface}",
                 f" ip address {ip} {mask}",
                 " dhcp select relay"]
        lines += [f" dhcp relay server-address {s}" for s in servers]
        lines.append(" quit")
        b.block("配置 DHCP 中继", lines)
        b.note("中继模式下由外部服务器分配地址，本机不建地址池。")
        b.block("保存配置", _SAVE)
        return b.done()

    pool = B.ensure(p.get("pool"), "地址池名称")
    network = B.ensure(p.get("network"), "地址池网段")
    lines = [_ENTER, "dhcp enable",
             f"dhcp server ip-pool {pool}",
             f" network {network} mask {B.prefix_to_dotted(mask)}"]
    if p.get("gateway"):
        lines.append(f" gateway-list {p['gateway'].strip()}")
    dns = B.parse_ip_list(p.get("dns", ""))
    if dns:
        lines.append(f" dns-list {' '.join(dns)}")
    excludes = B.parse_ip_list(p.get("exclude", ""))
    if excludes:
        if len(excludes) > 64:
            b.warn(f"排除地址展开后共 {len(excludes)} 个，超过单条命令 64 个的上限，已自动分行。")
            for i in range(0, len(excludes), 64):
                lines.append(f" forbidden-ip {' '.join(excludes[i:i + 64])}")
        else:
            lines.append(f" forbidden-ip {' '.join(excludes)}")
    lines.append(" quit")
    lines += [f"interface {iface}",
              f" ip address {ip} {mask}",
              f" dhcp server apply ip-pool {pool}",
              " quit"]
    b.block("配置 DHCP 服务器", lines)
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
        cmd = f"ip route-static {r.net} {r.prefix} {r.nexthop}"
        if r.pref:
            cmd += f" preference {r.pref}"
        lines.append(cmd)
    b.block("配置静态路由", lines,
            comment="H3C 静态路由默认优先级 60，数值越大优先级越低。"
                    "未填写优先级时不下发该字段，由设备使用默认值。")

    lines = [_ENTER]
    for r in routes:
        lines.append(f"display ip routing-table {r.net}/{r.prefix} verbose")
    b.block("下发后核对", lines,
            comment="确认路由已进入路由表且下一跳可达。")
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
        acl_id = "3000" if advanced else "2000"
        b.note(f"未填写 ACL 编号，已默认使用 {acl_id}。")

    head = f"acl advanced {acl_id}" if advanced else f"acl basic {acl_id}"
    lines = [_ENTER, head, " step 10"]
    if p.get("desc"):
        lines.append(f" description {p['desc'].strip()}")
    for i, rule in enumerate(rules, start=1):
        step = i * 10
        parts = [f" rule {step} {rule.action}"]
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
            # 基础 ACL 只能匹配源地址；写了协议 / 目的地址设备会直接报错
            if rule.src.lower() == "any":
                parts.append("source any")
            else:
                net, plen = B.parse_net(rule.src)
                parts.append(f"source {net} {B.prefix_to_wildcard(plen)}")
        lines.append(" ".join(parts))
    lines.append(" quit")
    b.block("定义 ACL", lines,
            comment="Comware V5 使用 acl number 编号，V7 使用 acl basic / advanced。")

    if not advanced and any(r.dst.lower() != "any" or r.op for r in rules):
        b.warn("当前是基础 ACL（只能匹配源地址），规则里的目的地址与端口字段已按语法忽略；"
               "需要按五元组过滤请改用 3000-3999 的高级 ACL。")

    if any(r.action == "permit" for r in rules) and not any(r.action == "deny" for r in rules):
        b.warn("规则中没有 deny，ACL 默认动作将由设备决定，建议补一条显式拒绝。")

    apply_ports = ports.expand(p.get("apply_port", ""), VENDOR.key)
    if apply_ports:
        direction = p.get("direction", "inbound")
        b.block("应用到端口", [_ENTER] + _if_blocks(apply_ports, [f"packet-filter {acl_id} {direction}"]))
        b.note(f"packet-filter 只能过滤进入端口 {direction} 方向的流量，请确认方向正确。")

    b.block("保存配置", _SAVE)
    return b.done()


def _mirror(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    group = B.ensure(p.get("group"), "镜像组号")
    monitor = ports.expand(p.get("monitor", ""), VENDOR.key)
    if not monitor:
        raise ValueError("必须指定监控端口。")

    lines = [_ENTER, f"mirroring-group {group} local"]
    vlan = (p.get("vlan") or "").strip()
    if vlan:
        lines.append(f"mirroring-group {group} mirroring-vlan {vlan} {p.get('direction', 'both')}")
        b.block("创建本地镜像组（VLAN 镜像）", lines,
                comment="较老的 Comware 版本只接受 inbound，写 both 会报错；"
                        "下发前请用 mirroring-group ? 确认本机支持的取值。")
    else:
        b.block("创建本地镜像组", lines)
        source = ports.expand(p.get("source", ""), VENDOR.key)
        if not source:
            raise ValueError("必须指定被镜像端口。")
        body = [f"mirroring-group {group} mirroring-port {p.get('direction', 'both')}"]
        b.block("指定被镜像端口", [_ENTER] + _if_blocks(source, body))

    for name in monitor:
        b.block("指定监控端口", [
            _ENTER, f"interface {name}",
            f" mirroring-group {group} monitor-port",
            " quit",
        ], comment="监控口不要承载业务，接抓包主机或 IDS。")

    b.block("保存配置", _SAVE)
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
             f"interface Vlan-interface {vlan_id}",
             f" ip address {mgmt_ip} {mask}", " quit"]
    if p.get("gateway"):
        lines.append(f"ip route-static 0.0.0.0 0 {p['gateway'].strip()}")
    b.block("设备名与管理地址", lines)

    role = "network-admin" if str(level) == "15" else "network-operator"
    lines = [_ENTER,
             f"local-user {user} class manage",
             f" password simple {password}",
             " service-type ssh",
             f" authorization-attribute user-role {role}",
             " quit",
             "",
             "line vty 0 4",
             " authentication-mode scheme",
             " protocol inbound ssh",
             " quit",
             "ssh server enable",
             " undo telnet server enable"]
    b.block("管理员账号与 SSH", lines,
            comment="Comware V5 用 local-user 下的 level 3 指定权限，没有 user-role 概念。")
    b.warn("配置中的密码是明文，交付后请立即更换并改用本地保存的加密方式。")

    if p.get("ntp"):
        b.block("NTP 时间同步", [
            _ENTER, "ntp-service enable",
            f"ntp-service unicast-server {p['ntp'].strip()}",
        ], comment="时间不准会导致日志、证书和审计记录失去参考价值。")

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
        b.warn("SNMP v2c 团体字以明文传输，生产环境建议改用 v3。")
    elif ver == "v3":
        lines = [_ENTER, "snmp-agent",
                 "snmp-agent sys-info version v3",
                 "snmp-agent group v3 SCG-GROUP privacy",
                 "snmp-agent usm-user v3 scguser SCG-GROUP simple authentication-mode sha",
                 "snmp-agent usm-user v3 scguser SCG-GROUP privacy-mode aes128"]
        b.block("SNMP（v3）", lines,
                comment="此处仅给出骨架，认证与加密密码请在设备上交互输入，避免明文留在脚本里。")

    b.block("保存配置", _SAVE)
    return b.done()


# --------------------------------------------------------------------------
# 高可用
# --------------------------------------------------------------------------

def _stacking(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    member = B.ensure(p.get("member_id"), "成员号")
    priority = B.ensure(p.get("priority"), "优先级")
    domain = (p.get("domain") or "").strip()
    port2 = (p.get("port2") or "").strip()

    p1 = ports.expand(p.get("port1", ""), VENDOR.key)
    p2 = ports.expand(port2, VENDOR.key) if port2 else []
    if not p1:
        raise ValueError("必须指定堆叠口 1。")

    if str(member) != "1":
        b.block("修改成员号（执行后必须重启）", [
            _ENTER, f"irf member 1 renumber {member}",
        ], comment="仅当本机当前成员号与目标不一致时执行；改完立即 save 并重启。")
        b.warn("修改 IRF 成员号需要重启设备，重启前确认业务已切换。")

    lines = [_ENTER, f"irf member {member} priority {priority}"]
    if domain:
        lines.append(f"irf domain {domain}")
    b.block("设置成员优先级与域", lines,
            comment="优先级数值越大越优先成为主机；域号可避免不同 IRF 误合并。")

    lines = [_ENTER]
    for name in p1 + p2:
        lines += [f"interface {name}", " shutdown", " quit"]
    b.block("堆叠口置为关闭（配置前必须）", lines)

    lines = [_ENTER]
    for idx, name in enumerate(p1, start=1):
        lines += [f"irf-port {member}/{idx}",
                  f" port group interface {name}", " quit"]
    if p2:
        for idx, name in enumerate(p2, start=len(p1) + 1):
            lines += [f"irf-port {member}/{idx}",
                      f" port group interface {name}", " quit"]
    b.block("绑定 IRF 物理端口", lines,
            comment="同一成员的两个 IRF 端口不能绑定同一物理口。")

    lines = [_ENTER]
    for name in p1 + p2:
        lines += [f"interface {name}", " undo shutdown", " quit"]
    b.block("恢复堆叠口", lines)

    b.block("保存并激活 IRF", [
        _ENTER, "save force", "irf-port-configuration active",
    ], comment="激活瞬间主备会重新选举，业务会有秒级抖动。")

    if not p2:
        b.note("只配置了一条堆叠链路，链路故障会导致 IRF 分裂，建议配置第二条。")
    return b.done()


def _gateway(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    if p.get("proto") == "hsrp":
        raise ValueError("H3C 不支持 HSRP，请选择 VRRP。")

    vlan_id = B.ensure(p.get("vlan_id"), "接口 VLAN")
    group = B.ensure(p.get("group"), "备份组号")
    vip = B.ensure(p.get("virtual_ip"), "虚拟网关 IP")
    iface_ip = B.ensure(p.get("interface_ip"), "本机接口 IP")
    mask = int(p.get("mask") or 24)
    priority = B.ensure(p.get("priority"), "优先级")

    lines = [_ENTER, f"interface Vlan-interface {vlan_id}",
             f" ip address {iface_ip} {mask}",
             f" vrrp vrid {group} virtual-ip {vip}",
             f" vrrp vrid {group} priority {priority}"]
    if B.truthy(p.get("preempt")):
        lines.append(f" vrrp vrid {group} preempt-mode delay 5")
    lines.append(" quit")
    b.block("配置 VRRP 虚拟网关", lines,
            comment="两台设备的 vrid、虚拟 IP 必须完全一致，否则会形成双主。")

    track = (p.get("track") or "").strip()
    if track:
        trk = ports.expand(track, VENDOR.key)[0]
        b.block("上行链路监控", [
            _ENTER,
            f"track 1 interface {trk}",
            f"interface Vlan-interface {vlan_id}",
            f" vrrp vrid {group} track 1 reduced 30",
            " quit",
        ], comment="上行口 down 时优先级自动降低 30，触发备机抢占。"
                   "不同软件版本的 track 语法略有差异，下发前请核对。")

    b.block("保存配置", _SAVE)
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

    lines = [_ENTER, f"port-isolate group {group}", " quit"]
    if B.truthy(p.get("l3_isolate")):
        lines.append("port-isolate mode all")
    b.block("创建隔离组", lines,
            comment="隔离组编号 1-8。port-isolate mode all 表示二层三层都隔离，"
                    "缺省只隔离二层、隔离口之间仍可经网关互访。")

    b.block("端口加入隔离组",
            [_ENTER] + _if_blocks(port_list, [f"port-isolate enable group {group}"]))
    b.block("保存配置", _SAVE)

    uplink = ports.expand(p.get("uplink", ""), VENDOR.key)
    if uplink:
        b.note(f"上行口 {', '.join(uplink)} 未加入隔离组，隔离口与它之间可以正常通信。")
    else:
        b.warn("没有指定上行口。请确认至少有一个端口不在隔离组内，否则隔离口无法与外部通信。")
    b.warn("H3C 在 IRF 组网环境下不支持端口隔离功能，堆叠组网请改用其他方式。")
    return b.done()


def _port_security(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if not port_list:
        raise ValueError("请填写受控端口。")
    max_mac = B.ensure(p.get("max_mac") or "3", "最大 MAC 数")
    action = p.get("action", "restrict")
    vlan = str(p.get("vlan") or "").strip()

    lines = [_ENTER, "port-security enable"]
    aging = str(p.get("aging") or "").strip()
    if aging:
        lines.append(f"port-security timer autolearn aging {aging}")
    if action == "shutdown":
        lines.append("port-security timer disableport 30")
    b.block("全局开启端口安全", lines,
            comment="端口安全是全局开关 + 端口参数两级配置，缺一不可。")

    body = [f"port-security max-mac-count {max_mac}",
            "port-security port-mode autolearn"]
    if action == "shutdown":
        body.append("port-security intrusion-mode disableport-temporarily")
    elif action == "restrict":
        body.append("port-security intrusion-mode blockmac")
    b.block("端口安全参数", [_ENTER] + _if_blocks(port_list, body),
            comment="autolearn 模式下端口先自动学习，学到最大值后再来新 MAC 就触发违例。")

    if action == "protect":
        b.note("「只丢弃不告警」在 H3C 上就是默认行为，因此没有额外生成 intrusion-mode。")
    elif action == "shutdown":
        b.warn("违例动作选了关闭端口，端口会被暂时关闭 30 秒，业务有中断风险。")

    macs = B.parse_macs(p.get("macs", ""))
    if macs:
        bind_vlan = vlan or "1"
        if not vlan:
            b.warn("未填写绑定所属 VLAN，已按 VLAN 1 生成，请确认后修改。")
        lines = [_ENTER]
        for idx, name in enumerate(port_list, start=1):
            lines.append(f"interface {name}")
            for mac in macs:
                lines.append(f" port-security mac-address security sticky "
                             f"{B.mac_dashed(mac)} vlan {bind_vlan}")
            lines.append(" quit")
        b.block("固化静态安全 MAC", lines,
                comment="sticky 表示这条地址是固化下来的，端口 down 或重启后依然保留。")
    elif B.truthy(p.get("sticky")):
        b.note("选了自动学习模式，设备会自己把学到的 MAC 转成安全 MAC，"
               "需要固定下来时可以用 display port-security 查到后再固化。")

    b.block("保存配置", _SAVE)
    b.warn("端口安全不能与 802.1X、MAC 认证同时配置；开启时端口上的这两项配置会被重置。")
    b.warn("安全端口不能作为镜像目的口，也不能加入聚合组。")
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
        suffix = f"ratio {value}"
    else:
        if not value.isdigit():
            raise ValueError("pps 阈值必须是整数。")
        suffix = f"pps {value}"

    body: list[str] = []
    if B.truthy(p.get("bcast")):
        body.append(f"broadcast-suppression {suffix}")
    if B.truthy(p.get("mcast")):
        body.append(f"multicast-suppression {suffix}")
    if B.truthy(p.get("ucast")):
        body.append(f"unicast-suppression {suffix}")
    if not body:
        raise ValueError("广播 / 组播 / 未知单播至少要勾一项。")

    b.block("配置风暴抑制", [_ENTER] + _if_blocks(port_list, body),
            comment="H3C 的百分比关键字是 ratio（不是 percent）。"
                    "pps 与 kbps 模式下设备会按芯片步长取整，实际生效值可能与填写的略有出入。")
    b.block("保存配置", _SAVE)
    b.warn("同一类型报文的 broadcast-suppression 与 storm-constrain 不能同时配置，否则抑制效果不确定。")
    if mode == "percent":
        b.note("百分比模式下 10% 是较常见的保守取值；接入口可按现场广播量调整。")
    return b.done()


def _loop_guard(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans_spec = (p.get("vlans") or "").strip()
    interval = B.ensure(p.get("interval") or "10", "检测周期")
    action = p.get("action", "shutdown")

    vlan_arg = "all" if not vlans_spec else B.compact_vlans(
        B.parse_vlans(vlans_spec), " ", " to ")

    lines = [_ENTER, f"loopback-detection global enable vlan {vlan_arg}",
             f"loopback-detection interval-time {interval}"]
    if action == "shutdown":
        lines.append("loopback-detection global action shutdown")
    elif action == "block":
        lines.append("loopback-detection global action block")
    b.block("全局开启环路检测", lines,
            comment="环路检测默认只记日志、不动端口，所以要显式配置处理动作。"
                    "检测周期默认 30 秒，这里按填写值下发。")

    port_list = ports.expand(p.get("ports", ""), VENDOR.key)
    if port_list:
        body = [f"loopback-detection enable vlan {vlan_arg}"]
        _add_intrusion(body, action)
        b.block("按端口开启环路检测", [_ENTER] + _if_blocks(port_list, body))

    if B.truthy(p.get("bpdu_guard")):
        b.block("全局开启 BPDU 保护", [_ENTER, "stp bpdu-protection"],
                comment="边缘端口一旦收到 BPDU，说明下面接的不是终端而是交换机，"
                        "设备会直接把该端口关闭。")
        edge = ports.expand(p.get("edge_ports", ""), VENDOR.key)
        if edge:
            b.block("边缘端口设为边缘口",
                    [_ENTER] + _if_blocks(edge, ["stp edged-port"]),
                    comment="只有边缘端口才受 BPDU 保护管辖；上行口不要配边缘端口。")
        else:
            b.warn("开了 BPDU 保护但没有指定边缘端口，保护不会生效。")

    b.block("保存配置", _SAVE)
    b.note("被环路检测关掉的端口，可以在接口视图下 shutdown 再 undo shutdown 恢复。")
    return b.done()


def _add_intrusion(body: list, action: str) -> None:
    """环路检测的端口级处理动作（shutdown 时全局已配，端口可再明确一次）。"""
    if action == "shutdown":
        body.append("loopback-detection action shutdown")
    elif action == "block":
        body.append("loopback-detection action block")
    else:
        body.append("loopback-detection action no-learning")


# --------------------------------------------------------------------------
# 第二批：接入安全
# --------------------------------------------------------------------------

def _dhcp_snooping(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    lines = [_ENTER, "dhcp snooping enable"]
    if B.truthy(p.get("check_mac")):
        lines.append("dhcp snooping check mac-address")
    b.block("全局开启 DHCP Snooping", lines,
            comment="check mac-address 会把 DHCP 请求里的客户端 MAC 和报文源 MAC 比对，"
                    "用于拦住伪造请求。")

    lines = [_ENTER]
    for v in vlans:
        lines += [f"vlan {v}", " dhcp snooping enable", " quit"]
    b.block("按 VLAN 开启", lines,
            comment="全局开关和 VLAN 开关都要打开，DHCP Snooping 才会在该 VLAN 生效。")

    trust = ports.expand(p.get("trust_ports", ""), VENDOR.key)
    if trust:
        b.block("指定信任端口",
                [_ENTER] + _if_blocks(trust, ["dhcp snooping trust"]),
                comment="只有信任端口上收到的 DHCP 应答才会被转发，其余一律丢弃。")
    else:
        b.warn("没有指定信任端口，全网 DHCP 应答都会被丢弃，客户端拿不到地址。")

    b.block("保存配置", _SAVE)
    b.note("DHCP Snooping 产生的绑定表是动态 ARP 检测和 IP 源防护的判断依据，建议一起配。")
    return b.done()


def _arp_guard(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    lines = [_ENTER]
    if B.truthy(p.get("validate")):
        lines.append("arp detection validate dst-mac ip src-mac")
    for v in vlans:
        lines += [f"vlan {v}", " arp detection enable", " quit"]
    b.block("开启 ARP 检测", lines,
            comment="arp detection validate 校验 ARP 报文的 MAC 与 IP 是否自相矛盾，"
                    "能挡住大部分 ARP 欺骗。")

    trust = ports.expand(p.get("trust_ports", ""), VENDOR.key)
    if trust:
        b.block("指定信任端口",
                [_ENTER] + _if_blocks(trust, ["arp detection trust"]),
                comment="上行口必须设为信任口，否则来自网关的正常 ARP 也会被丢弃。")
    else:
        b.warn("没有指定信任端口，上行链路的 ARP 报文会被误拦，请务必设置。")

    if B.truthy(p.get("ip_source_guard")):
        users = ports.expand(p.get("user_ports", ""), VENDOR.key)
        if not users:
            b.warn("没有填写用户接入口，IP 源防护无法逐口下发，已跳过。")
        else:
            b.block("用户口开启 IP 源防护",
                    [_ENTER] + _if_blocks(users, ["ip verify source ip-address mac-address"]),
                    comment="按 DHCP Snooping 绑定表校验源 IP 与源 MAC，"
                            "终端私自改 IP 后报文会被直接丢弃。需要先配好 DHCP Snooping。")
        b.warn("IP 源防护依赖 DHCP Snooping 绑定表；如果终端用静态 IP，"
               "需要在设备上补一条静态绑定（ip source binding），否则它会连不上网。")

    b.block("保存配置", _SAVE)
    return b.done()


# --------------------------------------------------------------------------
# 第二批：三层 / 组播
# --------------------------------------------------------------------------

def _igmp_snooping(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    vlans = B.parse_vlans(B.ensure(p.get("vlans"), "生效 VLAN"))

    lines = [_ENTER, "igmp-snooping"]
    for v in vlans:
        lines += [f"vlan {v}", " igmp-snooping enable", " quit"]
    b.block("开启 IGMP Snooping", lines,
            comment="全局打开 IGMP Snooping 后，还要在具体 VLAN 里再打开一次。")

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
            lines.append(f"interface Vlan-interface {v}")
            for name in router_ports:
                lines.append(f" igmp-snooping router-port interface {name}")
            lines.append(" quit")
        b.block("静态指定路由器端口", lines,
                comment="组播流量从这些口进来。不指定的话设备靠动态学习识别，"
                        "上行链路不稳定时表项容易丢。")

    if B.truthy(p.get("fast_leave")):
        lines = [_ENTER]
        for v in vlans:
            lines += [f"vlan {v}", " igmp-snooping fast-leave", " quit"]
        b.block("开启快速离开", lines,
                comment="收到离开报文立即停止转发，减少换台延迟。")

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

    lines = [_ENTER, f"ospf {process} router-id {router_id}", " quit"]
    b.block("启用 OSPF 并指定 Router ID", lines,
            comment="Router ID 建议手工指定成 Loopback 地址，不要依赖设备自动选举。")

    lines = [_ENTER, f"ospf {process}"]
    for net, plen, area in nets:
        lines += [f" area {area}",
                  f"  network {net} {B.prefix_to_wildcard(plen)}",
                  "  quit"]
    lines.append(" quit")
    b.block("宣告网段", lines,
            comment="Comware 的 area 视图下 network 用反掩码（通配符掩码）。")

    if p.get("auth") == "md5":
        key = B.ensure(p.get("auth_key"), "验证密钥")
        areas = []
        for _, _, area in nets:
            if area not in areas:
                areas.append(area)
        lines = [_ENTER, f"ospf {process}"]
        for area in areas:
            lines += [f" area {area}", "  authentication-mode md5", "  quit"]
        lines.append(" quit")
        b.block("开启区域验证", lines,
                comment="区域验证要在同一区域的所有设备上保持一致，否则邻居建不起来。")
        b.warn(f"MD5 验证密钥 {key} 是明文写在脚本里的，下发后请及时在设备上改成密文保存。")

    passive = B.split_ifaces(p.get("passive"))
    if passive:
        lines = [_ENTER, f"ospf {process}"]
        lines += [f" silent-interface {name}" for name in passive]
        lines.append(" quit")
        b.block("设置被动接口", lines,
                comment="被动接口不发 Hello 报文，但网段照样宣告出去，适合接终端的接口。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由", [_ENTER, "display ospf peer", "display ospf routing"],
            comment="两步确认：先看邻居是不是 Full，再看路由有没有进表。")
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
    if int(cir) % 64:
        b.warn(f"CIR {cir} 不是 64 的整数倍，H3C 要求取 64 的倍数，建议改成 "
               f"{int(cir) // 64 * 64} 或 {int(cir) // 64 * 64 + 64}。")
    direction = p.get("direction", "inbound")
    cbs = str(p.get("cbs") or "").strip()

    cmd = f"qos lr {direction} cir {cir}"
    if cbs:
        if not cbs.isdigit():
            raise ValueError("突发尺寸必须是整数（单位字节）。")
        cmd += f" cbs {cbs}"
    b.block("端口限速", [_ENTER] + _if_blocks(port_list, [cmd]),
            comment="qos lr 是接口级物理限速，直接按令牌桶对进出端口的流量整形。")
    b.block("核对限速结果", [_ENTER, "display qos lr interface"],
            comment="确认 CIR 已经按要求生效。")
    b.block("保存配置", _SAVE)
    b.note(f"当前限制的是{('入方向（用户上行）' if direction == 'inbound' else '出方向（用户下行）')}。")
    return b.done()


def _inspect(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    if not any(B.truthy(p.get(k)) for k in
               ("basic", "interface", "l2", "l3", "security", "hardware", "logs")):
        raise ValueError("至少勾选一类巡检内容。")

    if B.truthy(p.get("basic")):
        b.block("设备信息与资源", [
            "display version", "display device", "display device manuinfo",
            "display clock", "display cpu-usage", "display memory",
            "display boot-loader", "display startup", "display fan",
            "display power", "display temperature",
        ], comment="开局先留一份基线，出问题时拿它来对比。")

    if B.truthy(p.get("interface")):
        b.block("接口与流量", [
            "display interface brief", "display interface",
            "display counters inbound interface", "display counters outbound interface",
            "display counters rate inbound interface", "display counters rate outbound interface",
            "display packet-drop interface", "display link-delay",
        ], comment="看错包、丢包和速率，是判断链路质量最快的一组命令。")

    if B.truthy(p.get("l2")):
        b.block("二层转发表", [
            "display vlan brief", "display interface brief | include UP",
            "display mac-address", "display mac-address mac-move",
            "display link-aggregation summary", "display link-aggregation verbose",
            "display stp brief", "display stp root", "display stp region-configuration",
            "display lldp neighbor-information",
        ], comment="mac-move 能直接看出 MAC 漂移，是定位环路最直接的线索。")

    if B.truthy(p.get("l3")):
        b.block("三层与地址", [
            "display ip routing-table", "display ip routing-table statistics",
            "display arp all", "display arp count", "display ip interface brief",
            "display dhcp server ip-in-use", "display dhcp relay statistics",
            "display vrrp brief", "display ospf peer", "display ospf routing",
        ], comment="先看路由表里有没有你要的那条，再看 ARP 学没学到对端。")

    if B.truthy(p.get("security")):
        b.block("安全与接入", [
            "display dhcp snooping binding", "display dhcp snooping statistics",
            "display arp detection statistics", "display port-security",
            "display port-security intrusion", "display ip verify source",
            "display dot1x", "display mac-address security",
            "display acl all", "display packet-filter statistics",
        ], comment="命中计数长期为 0 的规则，往往说明它根本没生效。")

    if B.truthy(p.get("hardware")):
        b.block("硬件与光模块", [
            "display transceiver interface", "display transceiver diagnosis interface",
            "display transceiver manuinfo interface", "display poe interface",
            "display poe interface power", "display power-supply",
        ], comment="光模块收发功率是排查「链路 up 但丢包」的关键数据。")

    if B.truthy(p.get("logs")):
        b.block("日志与告警", [
            "display logbuffer summary", "display logbuffer",
            "display trapbuffer", "display exception", "display reboot-info",
            "display diag",
        ], comment="异常重启记录和日志缓冲是定位设备自身问题的入口。")

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
        b.block("先保存当前配置", [_ENTER, "save force"],
                comment="不先保存的话，备份走的是上一次保存过的启动配置，"
                        "而不是你现在跑着的运行配置。")

    user = str(p.get("user") or "").strip()
    password = str(p.get("password") or "").strip()
    if proto == "ftp":
        if not user or not password:
            raise ValueError("FTP 方式需要填写用户名和密码。")
        b.block("指定 FTP 服务器", [
            _ENTER, f"ftp {server}",
        ], comment="先登录 FTP 服务器，后续 put / get 才能执行。"
                   f"用户名 {user}，密码按提示输入，不要把密码写进脚本。")
        b.warn("脚本里不生成明文密码，登录 FTP 时请手工输入。")

    if action == "backup":
        b.block("备份配置到服务器", [
            _ENTER,
            f"tftp {server} put flash:/startup.cfg {filename}"
            if proto == "tftp" else f"ftp {server} put flash:/startup.cfg {filename}",
        ], comment="把设备上的启动配置文件上传到服务器。"
                   "部分型号的实际文件名是 flash:/config.cfg 或 flash:/vrpcfg.zip，"
                   "请先用 dir flash:/ 确认。")
        b.block("确认配置已保存", [_ENTER, "display saved-configuration"],
                comment="确认当前运行配置与已保存配置一致，避免备份到过期的版本。")
    else:
        b.block("从服务器恢复配置", [
            _ENTER,
            f"tftp {server} get {filename} flash:/startup.cfg"
            if proto == "tftp" else f"ftp {server} get {filename} flash:/startup.cfg",
        ], comment="把备份文件下载回设备。下载回来的文件会覆盖启动配置。")
        b.warn("恢复配置需要重启设备才会完全生效，且会覆盖现有配置，"
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
            "info-center format unicom",
        ], comment="没有时间戳的日志在排障时几乎用不上，这一条建议保持开启。")

    if server:
        lines = [_ENTER,
                 f"info-center loghost {server}",
                 f"info-center source default loghost level {level}"]
        if B.truthy(p.get("console")):
            lines.insert(1, f"info-center source default console level {level}")
        b.block("配置日志服务器", lines,
                comment=f"只把 {level} 及以上级别的日志送到服务器，避免刷屏。")

    if ntp:
        b.block("NTP 时间同步", [
            _ENTER, "ntp-service enable",
            f"ntp-service unicast-server {ntp}",
        ], comment="时钟不准，日志的先后顺序就不可信了，NTP 和日志要一起配。")

    b.block("保存配置", _SAVE)
    b.block("验证日志是否送达", [_ENTER, "display info-center",
                                 "display logbuffer summary"],
            comment="配完在设备上制造一条日志（比如插拔一个端口），"
                    "去服务器上确认收到了。")
    return b.done()


# --------------------------------------------------------------------------
# 第三批：动态路由与 MPLS VPN
# --------------------------------------------------------------------------

def _isis(p, scenario) -> GenResult:
    b = B.Builder(VENDOR.name, scenario.name)
    process = B.ensure(p.get("process") or "1", "进程号")
    net = B.ensure(p.get("net"), "NET 地址")
    level = p.get("level", "level-2")

    lines = [_ENTER, f"isis {process}", f" is-level {level}",
             f" cost-style {p.get('cost_style', 'wide')}",
             f" network-entity {net}", " quit"]
    b.block("启用 IS-IS 并配置 NET", lines,
            comment="官方建议 cost-style、is-level、network-entity 三条一起改时"
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
                comment="只能建单层邻接时用 isis circuit-level 限制，能少收一半的 PDU。")
    else:
        b.warn("没有填三层接口，IS-IS 起来了也建不了邻接。")

    if B.truthy(p.get("import_direct")):
        b.block("引入直连路由", [
            _ENTER, f"isis {process}", " address-family ipv4",
            "  import-route direct", "  quit", " quit",
        ], comment="import-route 必须敲在 address-family ipv4 视图里，"
                   "在 isis 主视图敲会报 Unrecognized command。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "display isis peer", "display isis route", "display isis lsdb"],
            comment="先看邻接是不是 Up，再看路由表里有没有学到对端网段。")
    b.note("同一台设备只能有一个 IS-IS 实例运行在 Level-2（含 Level-1-2）。")
    return b.done()


def _l3_ifaces(p, vendor_key) -> list[str]:
    """把「三层接口 VLAN」和「三层物理口」两个参数合成接口名列表。"""
    out: list[str] = []
    for v in B.split_ifaces(p.get("svlan")):
        out.append(B.l3_iface(vendor_key, v))
    out.extend(B.resolve_ifaces(p.get("ports"), vendor_key))
    return out


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

    lines += [" address-family ipv4 unicast"]
    for peer in peers:
        lines.append(f"  peer {peer.ip} enable")
    for net, plen in nets:
        lines.append(f"  network {net} {B.prefix_to_dotted(plen)}")
    if B.truthy(p.get("import_direct")):
        lines.append("  import-route direct")
    lines += ["  quit", " quit"]
    b.block("配置 BGP 与邻居", lines,
            comment="Comware V7 必须在 address-family ipv4 unicast 里再 peer enable 一次，"
                    "只创建邻居不激活，会话建不起来。")

    b.block("保存配置", _SAVE)
    b.block("核对邻居与路由",
            [_ENTER, "display bgp peer ipv4", "display bgp routing-table ipv4"],
            comment="State 要是 Established，路由表里要有你要的网段。")
    if B.truthy(p.get("md5")):
        b.warn("TCP MD5 密钥在脚本里是明文，两端必须一致，不一致会一直卡在 Connect。")
    b.note("IBGP 邻居建议用 Loopback 建，并配 peer <地址> connect-interface Loopback0。")
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
        _ENTER, f"interface {loop}", f" ip address {lsr} 32", " quit",
    ], comment="LSR ID 用 Loopback 地址最稳，只要它不 down，LDP 会话就不会断。")

    b.block("开启 MPLS 与 LDP", [
        _ENTER, f"mpls lsr-id {lsr}", "mpls ldp",
        f"interface {core_if}", f" ip address {core_ip} {core_mask}",
        " mpls enable", " mpls ldp enable", " quit",
    ], comment="公网侧接口要同时开 mpls enable 和 mpls ldp enable，"
               "少一条标签就发不出去。接 CE 的接口不要开 MPLS。")

    b.block("创建 VPN 实例", [
        _ENTER, f"ip vpn-instance {vrf}",
        f" route-distinguisher {rd}",
        f" vpn-target {rt_in} import-extcommunity",
        f" vpn-target {rt_out} export-extcommunity",
        " quit",
    ], comment="RD 保证不同 VPN 的同网段路由不冲突；"
               "RT 决定路由在哪些 VPN 之间互通，本机导入值要对上对端的导出值。")

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
        " address-family vpnv4",
        f"  peer {pe_peer} enable",
        f"  peer {pe_peer} next-hop-local",
        "  quit",
        f" ip vpn-instance {vrf}",
        f"  peer {ce_peer} as-number {ce_as}",
        "  address-family ipv4 unicast",
        f"   peer {ce_peer} enable",
        "   import-route direct",
        "   quit",
        "  quit",
        " quit",
    ], comment="vpnv4 地址族负责 PE 之间传 VPN 路由；"
               "ip vpn-instance 下面是 PE 与 CE 之间的私网邻居。"
               "next-hop-local 让 PE 把下一跳改成本机，避免对端迭代不出隧道。")

    b.block("保存配置", _SAVE)
    b.block("分段核对", [
        _ENTER,
        "display mpls ldp peer",
        "display mpls ldp lsp",
        "display ip vpn-instance",
        "display bgp peer vpnv4",
        "display bgp vpnv4 vpn-instance " + vrf,
    ], comment="按顺序查：LDP 邻居通了没、标签有没有下发、VPN 实例建了没、"
               "MP-BGP 邻居起来没、VPN 路由收没收到。")
    b.warn("MPLS 的前提是公网侧 IGP 已经跑通，而且两端的 Loopback 都要被宣告进 IGP，"
           "否则 LDP 会话建不起来。建议先配 OSPF 或 IS-IS 那两条场景。")
    b.warn("PE 与 CE 之间的接口不要再开 MPLS，也不要绑两个 VPN 实例。")
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
}


def generate(scenario_key: str, params: dict, scenario) -> GenResult:
    fn = _HANDLERS.get(scenario_key)
    if fn is None:
        raise ValueError(f"{VENDOR.name} 暂不支持场景：{scenario.name}")
    result = fn(params, scenario)
    for n in scenario.notes:
        result.notes.append(n)
    return result
