"""
M4 Verifier — Automated Pre/Post Check Engine.

Executes verification steps (ping, show commands) to validate network state
before and after configuration changes.
"""

import logging
import re
from typing import List, Dict, Optional, Any
from pydantic import BaseModel

from drivers.factory import DriverFactory
from drivers.base import BaseDriver

logger = logging.getLogger("deploy.verifier")


class VerificationCheck(BaseModel):
    """A single verification step."""
    name: str
    check_type: str  # connectivity | interface_up | command_output
    target: str      # IP to ping, interface name, or command to run
    expected: Optional[str] = None  # regex or state to match
    timeout: int = 5


class VerificationResult(BaseModel):
    """Result of a single check."""
    check: VerificationCheck
    pass_: bool
    details: str
    raw_output: str = ""


class Verifier:
    """
    Runs a suite of verification checks on a device.
    """

    def __init__(self, driver: BaseDriver):
        self.driver = driver

    def run_checks(self, checks: List[VerificationCheck]) -> List[VerificationResult]:
        """Run a list of checks and return results."""
        results = []
        for check in checks:
            try:
                if check.check_type == "connectivity":
                    res = self._check_connectivity(check)
                elif check.check_type == "interface_up":
                    res = self._check_interface_up(check)
                elif check.check_type == "command_output":
                    res = self._check_command_output(check)
                else:
                    res = VerificationResult(
                        check=check,
                        pass_=False,
                        details=f"Unknown check type: {check.check_type}"
                    )
                results.append(res)
            except Exception as e:
                logger.error(f"Check {check.name} failed: {e}")
                results.append(VerificationResult(
                    check=check,
                    pass_=False,
                    details=f"Execution error: {str(e)}"
                ))
        return results

    def _check_connectivity(self, check: VerificationCheck) -> VerificationResult:
        """Run a ping check."""
        cmd = f"ping {check.target}"
        output = self.driver.run_command(cmd)
        
        # Simple success heuristic for Cisco/Huawei/Juniper
        # Look for "!!!", "Success rate is 100", "0% packet loss"
        success = False
        if "!" in output: # Cisco/Generic
            success = True
        elif "Success rate is 100" in output: # Cisco
            success = True
        elif "0% packet loss" in output: # Linux/Juniper
            success = True
        
        return VerificationResult(
            check=check,
            pass_=success,
            details=f"Ping result: {'Pass' if success else 'Fail'}",
            raw_output=output
        )

    def _check_interface_up(self, check: VerificationCheck) -> VerificationResult:
        """Check if an interface is UP/UP."""
        # Use simple show command because get_interfaces() might be cached or heavy
        cmd = f"show interface {check.target} brief" # Cisco-ish default
        # Ideally use driver.get_interfaces() for vendor agnostic, but that fetches ALL
        
        # Let's use get_interfaces() for reliability across vendors
        ifaces = self.driver.get_interfaces()
        target_if = None
        for i in ifaces:
            if i.name == check.target:
                target_if = i
                break
        
        if not target_if:
            return VerificationResult(
                check=check,
                pass_=False,
                details=f"Interface {check.target} not found"
            )
            
        is_up = target_if.status.lower() == "up"
        return VerificationResult(
            check=check,
            pass_=is_up,
            details=f"Status: {target_if.status}",
            raw_output=str(target_if)
        )

    def _check_command_output(self, check: VerificationCheck) -> VerificationResult:
        """Run a command and check if output matches regex."""
        output = self.driver.run_command(check.target)
        
        passed = False
        if check.expected:
            if re.search(check.expected, output, re.MULTILINE):
                passed = True
        else:
            # If no expectation, just running it successfully is a pass
            passed = True
            
        return VerificationResult(
            check=check,
            pass_=passed,
            details="Output matched expected pattern" if passed else "Output did not match",
            raw_output=output
        )
