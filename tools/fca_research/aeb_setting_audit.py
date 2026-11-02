#!/usr/bin/env python3
"""Offline Hyundai ADRV_0x160 AEB_SETTING audit from archived rlogs.

No vehicle I/O, no ECU writes, no CAN transmission, no control changes.
Signal 24|2@1+ is common to pinned carrot and sunnypilot DBCs.
Values are raw enum numbers, NOT an assertion of physical AEB availability.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

ADR = 0x160
SCC = 0x1A0

def decode_aeb_setting(payload: bytes) -> int:
    if len(payload) != 16:
        raise ValueError("ADRV_0x160 must be 16 bytes")
    return (payload[3] >> 0) & 0b11  # DBC: bit 24, length 2, little endian

def summarize(events, label):
    values = Counter()
    scc = Counter()
    seconds = defaultdict(Counter)
    first_ns = None
    errors = 0
    for event in events:
        try:
            direction = event.which()
        except Exception:
            continue
        if direction not in ("can", "sendcan"):
            continue
        ns = int(event.logMonoTime)
        if first_ns is None:
            first_ns = ns
        second = max(0, (ns - first_ns) // 1_000_000_000)
        for frame in getattr(event, direction):
            addr = int(frame.address)
            bus = int(frame.src)
            if addr == ADR:
                try:
                    val = decode_aeb_setting(bytes(frame.dat))
                except ValueError:
                    errors += 1
                    continue
                values[(direction, bus, val)] += 1
                seconds[second][(direction, bus, val)] += 1
            elif addr == SCC:
                scc[(direction, bus)] += 1
    return {
        "segment": label,
        "aeb_setting": [
            {"direction": d, "bus": b, "raw_value": v, "frames": n}
            for (d, b, v), n in sorted(values.items())
        ],
        "scc_control": [
            {"direction": d, "bus": b, "frames": n}
            for (d, b), n in sorted(scc.items())
        ],
        "invalid_0x160_lengths": errors,
        "per_second_aeb_setting": [
            {"second": int(t), "values": [
                {"direction": d, "bus": b, "raw_value": v, "frames": n}
                for (d, b, v), n in sorted(counter.items())
            ]}
            for t, counter in sorted(seconds.items())
        ],
    }

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("logs", type=Path, nargs="+")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    from openpilot.tools.lib.logreader import LogReader
    result = {
        "type": "read_only_aeb_setting_bitfield_audit",
        "signal_definition": "ADRV_0x160 AEB_SETTING 24|2@1+",
        "warning": "Raw bit values do not prove AEB status, ECU origin, or physical braking. Bus 128/192 may be Panda echoes.",
        "segments": [],
    }
    for path in args.logs:
        if not path.is_file():
            p.error(f"Missing local RLOG: {path}")
        item = summarize(LogReader(str(path)), path.name)
        result["segments"].append(item)
        tx = sum(x["frames"] for x in item["aeb_setting"] if x["direction"] == "sendcan")
        rx = sum(x["frames"] for x in item["aeb_setting"] if x["direction"] == "can")
        print(f"{path.name}: 0x160 sendcan={tx}, can={rx}, malformed={item['invalid_0x160_lengths']}")
        for entry in item["aeb_setting"]:
            print(f"  {entry['direction']} bus={entry['bus']} AEB_SETTING={entry['raw_value']} frames={entry['frames']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Saved:", args.output)
    print("OEM AEB functional validation: NOT PERFORMED")

if __name__ == "__main__":
    main()
