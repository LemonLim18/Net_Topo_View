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
        return f"""\
! ===== Day-0 Bootstrap: {p['hostname']} (Cisco IOS) =====
enable
configure terminal
!
hostname {p['hostname']}
!
interface {p['mgmt_interface']}
 ip address {p['mgmt_ip']} {p['subnet_mask']}
 no shutdown
 exit
!
ip default-gateway {p['gateway']}
!
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
ip route-static 0.0.0.0 0.0.0.0 {p['gateway']}
#
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
return
save force
"""

    # ------------------------------------------------------------------
    # Juniper JunOS
    # ------------------------------------------------------------------

    def _juniper_junos(self, p: dict) -> str:
        prefix_len = self._prefix_from_mask(p['subnet_mask'])
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
set routing-options static route 0.0.0.0/0 next-hop {p['gateway']}
#
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
