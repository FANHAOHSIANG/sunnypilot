#!/usr/bin/env python3
"""Offline route-level Hyundai FCA/SCC provenance report.

Reads already-recorded RLOGs through openpilot LogReader. Never accesses
hardware, sends CAN, changes Params, or modifies Panda safety.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

def describe_flags(value: int):
    try:
        from opendbc.car.hyundai.values import HyundaiFlags
        return sorted(x.name for x in HyundaiFlags if x.value and (value & int(x.value)) == int(x.value))
    except ImportError:
        return []

def summarize(events, name):
    flags = None
    longitudinal = None
    safety = []
    car = None
    counters = Counter()
    alert_types = Counter()
    aeb_samples = Counter()
    for e in events:
        kind = e.which()
        if kind == "carParams":
            cp = e.carParams
            car = str(cp.carFingerprint)
            flags = int(cp.flags)
            longitudinal = bool(cp.openpilotLongitudinalControl)
            safety = [{"model": str(s.safetyModel), "param": int(s.safetyParam)} for s in cp.safetyConfigs]
        elif kind in ("can", "sendcan"):
            for f in getattr(e, kind):
                addr = int(f.address)
                if addr not in (0x160, 0x175, 0x1A0):
                    continue
                bus = int(f.src)
                data = bytes(f.dat)
                if addr == 0x160 and len(data) == 16:
                    counters[(kind, bus, "AEB_SETTING", data[3] & 3)] += 1
                elif addr == 0x175 and len(data) >= 13:
                    # 96|2@1+ and 98|2@1+ (Intel little-endian)
                    b = data[12]
                    counters[(kind, bus, "FCA_AvlblSta", (b >> 2) & 3)] += 1
                    counters[(kind, bus, "FCA_EquipSta", b & 3)] += 1
                elif addr == 0x1A0:
                    counters[(kind, bus, "SCC_CONTROL", 0)] += 1
        elif kind == "carState":
            for field in ("stockFcw", "stockAeb"):
                if bool(getattr(e.carState, field, False)):
                    aeb_samples[field] += 1
        elif kind == "selfdriveState":
            alert = str(getattr(e.selfdriveState, "alertType", ""))
            if any(w in alert.lower() for w in ("fcw", "aeb", "collision")):
                alert_types[alert] += 1
    records = [{"direction": d, "bus": bus, "signal": sig, "raw_value": val, "count": count}
               for (d, bus, sig, val), count in sorted(counters.items())]
    has_car_params = flags is not None
    return {
        "segment": name,
        "carParams_present": has_car_params,
        "carFingerprint": car,
        "vehicleFlagsRaw": flags,
        "vehicleFlagNames": describe_flags(flags) if has_car_params else [],
        "openpilotLongitudinalControl": longitudinal,
        "safetyConfigs": safety,
        "canSignals": records,
        "stockWarningSamples": dict(aeb_samples),
        "openpilotAlertTypes": dict(alert_types),
        "caveat": "CarParams absent means vehicle flags cannot be inferred from CAN counters. Stock AEB flags can be unpopulated under CAN FD Alpha longitudinal."
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    for p in args.logs:
        if not p.is_file():
            ap.error(f"Missing local RLOG: {p}")
    from openpilot.tools.lib.logreader import LogReader
    results = [summarize(LogReader(str(p)), p.name) for p in args.logs]
    report = {"type": "passive_fca_vehicle_mode_provenance",
              "safety_status": "NOT APPROVED FOR AEB DEPLOYMENT",
              "segments": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    for s in results:
        print(f"{s['segment']}: carParams={s['carParams_present']} "
              f"flags={s['vehicleFlagsRaw']} long={s['openpilotLongitudinalControl']}")
        for rec in s["canSignals"]:
            if rec["signal"] == "AEB_SETTING":
                print(f"  {rec['direction']} bus={rec['bus']} AEB_SETTING={rec['raw_value']} n={rec['count']}")
    print("Saved:", args.output)
    print("WARNING: CAN/FCW status is not proof of OEM AEB actuation")

if __name__ == "__main__":
    main()
