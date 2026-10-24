#!/usr/bin/env python3
"""Offline source-level FCA/AEB regression gates for the user's Ioniq 5.

Conservative static tests. PASS does not prove OEM FCW/AEB functional.
Does not import vehicle runtime or transmit CAN.
"""
from pathlib import Path
import argparse
import re
import sys

def check(root: Path):
    base = root / "opendbc_repo/opendbc"
    failures = []
    checks = [
        ("car/hyundai/interface.py", r"if CP\.openpilotLongitudinalControl and not \(\(CP\.flags & \(HyundaiFlags\.CANFD_CAMERA_SCC \| HyundaiFlags\.CAMERA_SCC\)\)", "camera SCC exemption from ECU disable"),
        ("car/hyundai/carcontroller.py", r"not \(\(self\.CP\.flags & HyundaiFlags\.CANFD_CAMERA_SCC\) or self\.ESCC\.enabled\)", "camera SCC exemption from Tester Present"),
        ("car/hyundai/carcontroller.py", r"create_fca_warning_light\(", "explicit FCA warning signaling"),
        ("car/hyundai/hyundaicanfd.py", r"['\"]AEB_SETTING['\"]\s*:\s*0x1", "AEB-disabled warning indicator remains visible"),
        ("safety/modes/hyundai_canfd.h", r"longitudinal_accel_checks\(", "longitudinal acceleration checks intact"),
        ("safety/modes/hyundai_canfd.h", r"check_relay", "stock ECU / relay conflict checks intact"),
        ("safety/modes/hyundai_canfd.h", r"0x1A0", "SCC_CONTROL safety handling intact"),
        ("safety/modes/hyundai_canfd.h", r"0x160", "ADAS/FCA signal relay handling intact"),
    ]
    for rel, pattern, description in checks:
        file = base / rel
        if not file.is_file():
            failures.append(f"MISSING {rel} [{description}]")
            continue
        if not re.search(pattern, file.read_text(encoding="utf-8"), re.MULTILINE):
            failures.append(f"UNVERIFIED {rel} [{description}]")
        else:
            print(f"PASS {description}")
    return failures

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    root = ap.parse_args().root
    errors = check(root)
    if errors:
        for error in errors:
            print("FAIL", error)
    print("SOURCE REGRESSION:", "FAILED" if errors else "PASSED")
    print("AEB FUNCTIONAL VALIDATION: NOT PERFORMED")
    print("ON-ROAD DEPLOYMENT: NOT AUTHORIZED")
    return bool(errors)

if __name__ == "__main__":
    sys.exit(main())
