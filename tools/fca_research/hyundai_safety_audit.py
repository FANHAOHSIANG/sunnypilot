#!/usr/bin/env python3
"""Read-only comparison of Hyundai SCC/FCA controls. Does not send CAN or change vehicle settings."""
from __future__ import annotations
import argparse
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATTERNS = {
    "disable_ecu": r"disable_ecu\(",
    "uds_communication_control": r"communication_control|DISABLE_RX_DISABLE_TX",
    "tester_present": r"make_tester_present_msg|\\x3[Ee]\\x80",
    "camera_scc": r"CANFD_CAMERA_SCC|CAMERA_SCC",
    "enhanced_scc": r"ENHANCED_SCC|ESCC\.enabled",
    "scc_control": r"SCC_CONTROL|0x1[Aa]0",
    "fca_warning": r"AEB_SETTING|create_fca_warning_light|ADRV_0x160|0x160",
    "stock_ecu_checks": r"check_relay|stock_ecu|relay_malfunction",
    "acceleration_checks": r"longitudinal_accel_checks",
}
TARGETS = {
    "interface": "opendbc/car/hyundai/interface.py",
    "controller": "opendbc/car/hyundai/carcontroller.py",
    "canfd": "opendbc/car/hyundai/hyundaicanfd.py",
    "safety_canfd": "opendbc/safety/modes/hyundai_canfd.h",
    "safety_common": "opendbc/safety/modes/hyundai_common.h",
}

def git_head(location: pathlib.Path) -> str:
    result = subprocess.run(["git", "-C", str(location), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "not available"

def report(label: str, path: pathlib.Path) -> None:
    print(f"\n## {label}: {path}")
    if not path.is_file():
        print("MISSING — cannot conclude safety behavior")
        return
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        tags = [key for key, pattern in PATTERNS.items() if re.search(pattern, line, flags=re.IGNORECASE)]
        if tags:
            print(f"{lineno:4} [{' / '.join(tags)}] {line.strip()[:170]}")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opendbc", type=pathlib.Path, default=ROOT / "opendbc_repo")
    parser.add_argument("--carrot-opendbc", type=pathlib.Path, help="Optional checked-out carrot-wip opendbc_repo root")
    args = parser.parse_args()
    print("READ ONLY / NO CAR CONNECTION / NOT AN AEB VALIDATION")
    print(f"Target opendbc commit: {git_head(args.opendbc)}")
    if args.carrot_opendbc:
        print(f"Carrot opendbc commit: {git_head(args.carrot_opendbc)}")
    for key, relative in TARGETS.items():
        report("target/" + key, args.opendbc / relative)
        if args.carrot_opendbc:
            report("carrot-wip/" + key, args.carrot_opendbc / relative)
    print("\nReview init() and periodic Tester Present as a pair. A warning lamp or FCW event is NOT proof of AEB function.")
    print("Do not disable safety checks, conceal warning indications, or deploy unvalidated code.")

if __name__ == "__main__":
    main()
