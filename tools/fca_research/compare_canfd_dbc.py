#!/usr/bin/env python3
"""Read-only Hyundai CAN FD DBC schema comparison. Does not send CAN frames.

Compare carrot-wip's vendored generated DBC with pinned sunnypilot's
opendbc source DBC. A signal mismatch is NOT proof that either DBC
correctly describes a particular Ioniq 5 ECU firmware variant.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

WATCH = {352: "ADRV_0x160", 373: "TCS", 416: "SCC_CONTROL"}
BO = re.compile(r"^BO_\s+(\d+)\s+(\S+):\s+(\d+)\s+")
SG = re.compile(r"^\s*SG_\s+(\w+)\s*:\s*(.*)$")

def parse_dbc(path: Path):
    result = {}
    current = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        msg = BO.match(line)
        if msg:
            ident = int(msg.group(1))
            current = ident if ident in WATCH else None
            if current is not None:
                result[current] = {"name": msg.group(2), "length": int(msg.group(3)), "signals": {}}
            continue
        signal = SG.match(line)
        if current is not None and signal:
            result[current]["signals"][signal.group(1)] = signal.group(2).strip()
    return result

def compare(carrot, sunny):
    output = []
    for ident, name in WATCH.items():
        c = carrot.get(ident)
        s = sunny.get(ident)
        if not c or not s:
            output.append({"can_id": ident, "name": name, "error": "Missing message in at least one DBC"})
            continue
        changes = []
        for signal in sorted(set(c["signals"]) | set(s["signals"])):
            a, b = c["signals"].get(signal), s["signals"].get(signal)
            if a != b:
                changes.append({"signal": signal, "carrot": a, "sunnypilot": b})
        output.append({"can_id": ident, "name": name, "carrot_length": c["length"],
                       "sunnypilot_length": s["length"], "carrot_signals": len(c["signals"]),
                       "sunnypilot_signals": len(s["signals"]), "different_signals": changes})
    return output

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--carrot", type=Path, required=True)
    p.add_argument("--sunnypilot", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    for path in (args.carrot, args.sunnypilot):
        if not path.is_file():
            p.error(f"Missing DBC: {path}")
    report = {
        "type": "hyundai_canfd_dbc_schema_diff",
        "sources": {"carrot": str(args.carrot), "sunnypilot": str(args.sunnypilot)},
        "warning": "Schema comparison only. No firmware variant validation or AEB functional proof.",
        "messages": compare(parse_dbc(args.carrot), parse_dbc(args.sunnypilot)),
    }
    if any("error" in m or m["carrot_length"] != m["sunnypilot_length"] for m in report["messages"]):
        raise SystemExit("DBC messages missing or frame lengths differ; manual review required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for msg in report["messages"]:
        print(f"{msg['name']} (0x{msg['can_id']:X}): carrot {msg['carrot_signals']}, "
              f"sunnypilot {msg['sunnypilot_signals']}, differences {len(msg['different_signals'])}")
    print("DBC SCHEMA COMPARISON COMPLETE; VEHICLE DEPLOYMENT NOT APPROVED")
    print("Saved:", args.output)

if __name__ == "__main__":
    main()
