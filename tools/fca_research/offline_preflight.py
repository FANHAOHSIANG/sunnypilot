#!/usr/bin/env python3
"""Fail-closed, offline preflight for Ioniq 5 FCA/AEB investigation.

This tool DOES NOT verify AEB effectiveness or authorize on-road testing.
It does not import openpilot, access CAN, or change vehicle parameters.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys

EXPECTED = {
    "panda": "4230530f5ec4c7f11971f868ae9930b3547346eb",
    "opendbc_repo": "1c51731784729da5ebaf24b3c34106bd27aaf94b",
}
CHECKS = {
    "opendbc/car/hyundai/interface.py": ("disable_ecu(", "CANFD_CAMERA_SCC", "ENHANCED_SCC"),
    "opendbc/car/hyundai/carcontroller.py": ("make_tester_present_msg(", "self.ESCC.enabled"),
    "opendbc/car/hyundai/hyundaicanfd.py": ("SCC_CONTROL", "AEB_SETTING", "ADRV_0x160"),
    "opendbc/safety/modes/hyundai_canfd.h": ("longitudinal_accel_checks(", "check_relay", "0x1A0"),
    "opendbc/safety/modes/hyundai_common.h": ("hyundai_longitudinal",),
}

def git_head(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None

def run(root: Path) -> int:
    failures: list[str] = []
    for submodule, sha in EXPECTED.items():
        path = root / submodule
        found = git_head(path)
        print(f"{submodule}: {found or 'MISSING'}")
        if found != sha:
            failures.append(f"{submodule}: expected {sha}, got {found or 'missing'}")
    opendbc = root / "opendbc_repo"
    for name, needles in CHECKS.items():
        path = opendbc / name
        if not path.is_file():
            failures.append(f"Missing {name}")
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in source:
                failures.append(f"{name}: no {needle!r}")
    print("\nSOURCE PREFLIGHT: " + ("FAILED" if failures else "PASSED"))
    for message in failures:
        print(" - " + message)
    print("VEHICLE / AEB DEPLOYMENT: NOT APPROVED")
    print("Requires passive vehicle logs, resolved vehicle flags, safety regression tests,")
    print("OEM DTC/ECU verification, and qualified closed-course validation.")
    return 1 if failures else 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    sys.exit(run(args.root))
