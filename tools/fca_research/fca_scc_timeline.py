#!/usr/bin/env python3
"""Offline, read-only FCA/SCC timeline from archived comma rlogs.

No CAN transmission, no Params writes, no safety or vehicle control changes.
The report intentionally omits GPS, images, and raw CAN payloads.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

WATCH = {0x160: "ADRV_0x160", 0x1A0: "SCC_CONTROL", 0x730: "ADAS_UDS", 0x7D0: "RADAR_UDS"}
DIAGNOSTIC_DISABLE = (b"\x03\x28\x83\x01", b"\x28\x83\x01")
KEEP_DISABLED = b"\x02\x3e\x80"

def extract_segment(events, label):
    counts = Counter()
    per_second = defaultdict(Counter)
    safety_events = Counter()
    diagnostic = Counter()
    base_time = None
    for event in events:
        try:
            kind = event.which()
        except Exception:
            continue
        if kind not in ("can", "sendcan", "carState", "onroadEvents"):
            continue
        ns = int(event.logMonoTime)
        if base_time is None:
            base_time = ns
        second = max(0, (ns - base_time) // 1_000_000_000)
        if kind in ("can", "sendcan"):
            for msg in getattr(event, kind):
                addr = int(msg.address)
                if addr not in WATCH:
                    continue
                bus = int(msg.src)
                key = (kind, WATCH[addr], bus)
                counts[key] += 1
                per_second[second][key] += 1
                if addr in (0x730, 0x7D0) and kind == "sendcan":
                    data = bytes(msg.dat)
                    if any(data.startswith(x) for x in DIAGNOSTIC_DISABLE):
                        diagnostic["disable_communication_request"] += 1
                    if data.startswith(KEEP_DISABLED):
                        diagnostic["tester_present_suppress_response"] += 1
        elif kind == "carState":
            cs = event.carState
            for field in ("stockFcw", "stockAeb"):
                try:
                    if bool(getattr(cs, field)):
                        safety_events[field + "_sample"] += 1
                        per_second[second][("event", field, 0)] += 1
                except (AttributeError, ValueError):
                    pass
        elif kind == "onroadEvents":
            for evt in event.onroadEvents:
                try:
                    name = str(evt.name)
                    if any(s in name.lower() for s in ("fcw", "aeb", "collision")):
                        safety_events["onroad_" + name] += 1
                except (AttributeError, ValueError):
                    pass
    def as_records(counter):
        return [{"direction": d, "message": n, "bus": b, "count": v}
                for (d, n, b), v in sorted(counter.items())]
    return {
        "segment": label,
        "counts": as_records(counts),
        "diagnostic_requests": dict(diagnostic),
        "safety_event_samples": dict(safety_events),
        "seconds_with_activity": [
            {"second": int(sec), "counts": as_records(items)}
            for sec, items in sorted(per_second.items())
            if any(k[1] in ("ADRV_0x160", "SCC_CONTROL", "stockFcw", "stockAeb") for k in items)
        ],
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logs", nargs="+", type=Path, help="Existing local rlog.zst files")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    from openpilot.tools.lib.logreader import LogReader
    segments = []
    for path in args.logs:
        if not path.is_file():
            ap.error(f"Missing file: {path}")
        segment = extract_segment(LogReader(str(path)), path.name)
        segments.append(segment)
        tx160 = sum(x["count"] for x in segment["counts"]
                    if x["direction"] == "sendcan" and x["message"] == "ADRV_0x160")
        tx1a0 = sum(x["count"] for x in segment["counts"]
                    if x["direction"] == "sendcan" and x["message"] == "SCC_CONTROL")
        print(f"{path.name}: TX 0x160={tx160}, TX 0x1A0={tx1a0}, "
              f"stockFcw={segment['safety_event_samples'].get('stockFcw_sample', 0)}, "
              f"stockAeb={segment['safety_event_samples'].get('stockAeb_sample', 0)}")
    report = {
        "type": "passive_fca_scc_timeline",
        "warning": "Not a functional AEB test. Missing stockFcw/stockAeb samples do not establish absence of functionality. Source buses 128/192 are not independently identified physical ECU sources.",
        "segments": segments,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {args.output}")

if __name__ == "__main__":
    main()
