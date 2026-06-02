"""
M0 — Onboarding Config Generator
Produces vendor-specific Day-0 CLI blocks that can be pasted into a device
console to enable SSH management access.
"""
from __future__ import annotations

import textwrap


class OnboardingConfigGenerator:
    """Generate minimal Day-0 bootstrap config for a given vendor."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, params: dict) -> str:
        """
        Return a ready-to-paste CLI block.

        Required keys in *params*:
            hostname       - device hostname
            mgmt_ip        - management IP address
            subnet_mask    - e.g. 255.255.255.0
            gateway        - default gateway IP
            mgmt_interface - interface name (e.g. GigabitEthernet0/0)
            vendor         - cisco_ios | cisco_xe | huawei_vrp | juniper_junos
            ssh_user       - SSH username to create
            ssh_password   - SSH password to set
            port           - SSH port (default 22, informational only)
        """
        vendor = params.get("vendor", "cisco_ios").lower()

        dispatch = {
            "cisco_ios": self._cisco_ios,
            "cisco_xe":  self._cisco_xe,
            "huawei_vrp": self._huawei_vrp,
            "juniper_junos": self._juniper_junos,
        }

        fn = dispatch.get(vendor, self._cisco_ios)
        config = fn(params)
        return textwrap.dedent(config).strip()

    # ------------------------------------------------------------------
    # Cisco IOS
    # ------------------------------------------------------------------

    def _cisco_ios(self, p: dict) -> str:
        common_services = self._cisco_common_services(p)
        role = str(p.get("role") or "").strip().lower()
        routing_block = "ip routing\n!\n" if role == "distribution" else ""
        gateway_block = f"ip default-gateway {p['gateway']}\n!\n" if p.get("gateway") else ""
        discovery_block = self._cisco_discovery_interfaces(p)
        return f"""\
! ===== Day-0 Bootstrap: {p['hostname']} (Cisco IOS) =====
enable
configure terminal
!
hostname {p['hostname']}
!
    {routing_block}interface {p['mgmt_interface']}
 ip address {p['mgmt_ip']} {p['subnet_mask']}
 no shutdown
 exit
!
{gateway_block}lldp run
cdp run
!
{discovery_block}
username {p['ssh_user']} privilege 15 secret {p['ssh_password']}
!
ip domain-name mgmt.local
crypto key generate rsa modulus 2048
!
ip ssh version 2
ip ssh time-out 60
ip ssh authentication-retries 3
!
line vty 0 4
 login local
 transport input ssh
 exit
!
{common_services}
service password-encryption
no ip http server
no ip http secure-server
!
end
write memory
"""

    # ------------------------------------------------------------------
    # Cisco IOS-XE  (adds NETCONF/RESTCONF for M1 crawling)
    # ------------------------------------------------------------------

    def _cisco_xe(self, p: dict) -> str:
        base = self._cisco_ios(p)
        xe_extra = f"""\

! ===== IOS-XE additions =====
configure terminal
!
netconf-yang
restconf
!
ip http server
ip http authentication local
ip http secure-server
!
end
write memory
"""
        return base + textwrap.dedent(xe_extra)

    # ------------------------------------------------------------------
    # Huawei VRP
    # ------------------------------------------------------------------

    def _huawei_vrp(self, p: dict) -> str:
        common_services = self._huawei_common_services(p)
        gateway_block = f"ip route-static 0.0.0.0 0.0.0.0 {p['gateway']}\n#\n" if p.get("gateway") else ""
        discovery_block = self._huawei_discovery_interfaces(p)
        return f"""\
# ===== Day-0 Bootstrap: {p['hostname']} (Huawei VRP) =====
system-view
#
sysname {p['hostname']}
#
interface {p['mgmt_interface']}
 ip address {p['mgmt_ip']} {self._cidr_from_mask(p['subnet_mask'])}
 undo shutdown
 quit
#
{gateway_block}lldp enable
lldp mdn enable
#
{discovery_block}
stelnet server enable
#
aaa
 local-user {p['ssh_user']} password irreversible-cipher {p['ssh_password']}
 local-user {p['ssh_user']} privilege level 15
 local-user {p['ssh_user']} service-type ssh
 quit
#
ssh user {p['ssh_user']} authentication-type password
ssh user {p['ssh_user']} service-type stelnet
#
user-interface vty 0 4
 authentication-mode aaa
 protocol inbound ssh
 user privilege level 15
 quit
#
{common_services}
return
save force
"""

    # ------------------------------------------------------------------
    # Juniper JunOS
    # ------------------------------------------------------------------

    def _juniper_junos(self, p: dict) -> str:
        prefix_len = self._prefix_from_mask(p['subnet_mask'])
        common_services = self._juniper_common_services(p)
        gateway_line = f"set routing-options static route 0.0.0.0/0 next-hop {p['gateway']}\n" if p.get("gateway") else ""
        discovery_block = self._juniper_discovery_interfaces(p)
        return f"""\
# ===== Day-0 Bootstrap: {p['hostname']} (Juniper JunOS) =====
configure
#
set system host-name {p['hostname']}
set system root-authentication plain-text-password-value {p['ssh_password']}
set system login user {p['ssh_user']} class super-user
set system login user {p['ssh_user']} authentication plain-text-password-value {p['ssh_password']}
set system services ssh root-login allow
set system services ssh protocol-version v2
#
set interfaces {p['mgmt_interface']} unit 0 family inet address {p['mgmt_ip']}/{prefix_len}
{gateway_line}set protocols lldp interface all
{discovery_block}
#
{common_services}
commit and-quit
"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cidr_from_mask(mask: str) -> int:
        """Convert dotted-decimal mask → prefix length for Huawei."""
        try:
            return sum(bin(int(o)).count('1') for o in mask.split('.'))
        except Exception:
            return 24

    @staticmethod
    def _prefix_from_mask(mask: str) -> int:
        """Convert dotted-decimal mask → prefix length."""
        try:
            return sum(bin(int(o)).count('1') for o in mask.split('.'))
        except Exception:
            return 24

    @staticmethod
    def _cisco_common_services(p: dict) -> str:
        lines = []
        snmp_community = p.get("snmp_community")
        if snmp_community:
            lines.append(f"snmp-server community {snmp_community} RO")
            if p.get("snmp_location"):
                lines.append(f"snmp-server location {p['snmp_location']}")
            if p.get("snmp_contact"):
                lines.append(f"snmp-server contact {p['snmp_contact']}")
            if p.get("snmp_host_ip"):
                lines.append(f"snmp-server host {p['snmp_host_ip']} version 2c {snmp_community}")
        if p.get("syslog_host_ip"):
            lines.append(f"logging host {p['syslog_host_ip']}")
        if p.get("ntp_server_ip"):
            lines.append(f"ntp server {p['ntp_server_ip']}")
        return "\n".join(lines)

    @staticmethod
    def _cisco_discovery_interfaces(p: dict) -> str:
        lines = []
        for iface in p.get("discovery_interfaces") or []:
            lines.extend([
                f"interface {iface}",
                " no shutdown",
                " lldp transmit",
                " lldp receive",
                " cdp enable",
                " exit",
                "!",
            ])
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _huawei_common_services(p: dict) -> str:
        lines = []
        snmp_community = p.get("snmp_huawei_community") or p.get("snmp_community")
        if snmp_community:
            lines.append(f"snmp-agent community read cipher {snmp_community}")
            if p.get("snmp_location"):
                lines.append(f"snmp-agent sys-info location {p['snmp_location']}")
            if p.get("snmp_contact"):
                lines.append(f"snmp-agent sys-info contact {p['snmp_contact']}")
            if p.get("snmp_host_ip"):
                lines.append(f"snmp-agent target-host trap address udp-domain {p['snmp_host_ip']} params securityname {snmp_community}")
        if p.get("syslog_host_ip"):
            lines.append(f"info-center loghost {p['syslog_host_ip']}")
        if p.get("ntp_server_ip"):
            lines.append(f"ntp-service unicast-server {p['ntp_server_ip']}")
        return "\n".join(lines)

    @staticmethod
    def _huawei_discovery_interfaces(p: dict) -> str:
        lines = []
        for iface in p.get("discovery_interfaces") or []:
            lines.extend([
                f"interface {iface}",
                " undo shutdown",
                " lldp enable",
                " quit",
                "#",
            ])
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _juniper_common_services(p: dict) -> str:
        lines = []
        snmp_community = p.get("snmp_community")
        if snmp_community:
            lines.append(f"set snmp community {snmp_community} authorization read-only")
            if p.get("snmp_location"):
                lines.append(f"set snmp location \"{p['snmp_location']}\"")
            if p.get("snmp_contact"):
                lines.append(f"set snmp contact \"{p['snmp_contact']}\"")
            if p.get("snmp_host_ip"):
                lines.append(f"set snmp trap-group NMS targets {p['snmp_host_ip']}")
        if p.get("syslog_host_ip"):
            lines.append(f"set system syslog host {p['syslog_host_ip']} any notice")
        if p.get("ntp_server_ip"):
            lines.append(f"set system ntp server {p['ntp_server_ip']}")
        return "\n".join(lines)

    @staticmethod
    def _juniper_discovery_interfaces(p: dict) -> str:
        lines = []
        for iface in p.get("discovery_interfaces") or []:
            lines.append(f"delete interfaces {iface} disable")
        return "\n".join(lines) + ("\n" if lines else "")
