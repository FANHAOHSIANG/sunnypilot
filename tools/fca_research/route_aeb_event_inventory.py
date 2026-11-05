#!/usr/bin/env python3
"""Read-only AEB/FCW event inventory from existing carrot-wip route logs.

No CAN TX, no vehicle commands, no changes to Panda or ECU.
For CAN FD Alpha longitudinal, carState.stockAeb may not be populated:
absence of that flag must NEVER be interpreted as proof AEB did not actuate.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

def scan(events, name):
    counters = Counter()
    first = []
    cp = {}
    for event in events:
        kind = event.which()
        t = int(event.logMonoTime)
        if kind == "carParams":
            c = event.carParams
            cp = {"carFingerprint": str(c.carFingerprint),
                  "openpilotLongitudinalControl": bool(c.openpilotLongitudinalControl),
                  "flags": int(c.flags),
                  "safetyConfigs": [{"safetyModel": str(s.safetyModel), "safetyParam": int(s.safetyParam)} for s in c.safetyConfigs]}
        elif kind == "carState":
            s = event.carState
            for field in ("stockAeb", "stockFcw"):
                if bool(getattr(s, field, False)):
                    counters[field] += 1
                    if len(first) < 20:
                        first.append({"monoTimeNs": t, "signal": field})
        elif kind in ("onroadEvents", "carEvents"):
            try:
                seq = getattr(event, kind)
            except Exception:
                continue
            for item in seq:
                label = str(getattr(item, "name", ""))
                if any(x in label.lower() for x in ("aeb", "fcw", "collision")):
                    counters["event_" + label] += 1
                    if len(first) < 20:
                        first.append({"monoTimeNs": t, "signal": "event_" + label})
    return {"file": name, "carParams": cp, "counts": dict(counters), "first_events": first}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logs", type=Path, nargs="+")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    for p in args.logs:
        if not p.is_file():
            ap.error(f"Missing RLOG: {p}")
    from openpilot.tools.lib.logreader import LogReader
    result = {"type": "passive_aeb_fcw_event_inventory",
              "warning": "stockAeb may not be populated for CAN FD Alpha longitudinal. AEB physical braking requires independent validation. No event is NOT proof of no AEB.",
              "segments": []}
    for p in args.logs:
        seg = scan(LogReader(str(p)), p.name)
        result["segments"].append(seg)
        print(p.name, "stockAeb samples", seg["counts"].get("stockAeb", 0),
              "stockFcw samples", seg["counts"].get("stockFcw", 0),
              "longitudinal", seg["carParams"].get("openpilotLongitudinalControl", "unknown"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("Saved:", args.output)

if __name__ == "__main__":
    main()
