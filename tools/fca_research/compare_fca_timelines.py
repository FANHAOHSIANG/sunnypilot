#!/usr/bin/env python3
"""Offline, non-actuating comparison of two FCA/SCC timeline JSON reports.

The input JSON must come from tools/fca_research/fca_scc_timeline.py.
No vehicle connection, ECU writes, or Panda modifications.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def aggregate(report):
    totals = Counter()
    seconds = 0
    stock = Counter()
    diagnostic = Counter()
    for segment in report.get("segments", []):
        for entry in segment.get("counts", []):
            key = (entry["direction"], entry["message"], int(entry["bus"]))
            totals[key] += int(entry["count"])
        for name, count in segment.get("safety_event_samples", {}).items():
            stock[name] += int(count)
        for name, count in segment.get("diagnostic_requests", {}).items():
            diagnostic[name] += int(count)
        stamps = segment.get("seconds_with_activity", [])
        if stamps:
            seconds += max(int(s["second"]) for s in stamps) + 1
    return totals, seconds, stock, diagnostic


def compare(carrot, sunny):
    carrot_totals, carrot_seconds, carrot_stock, carrot_diag = aggregate(carrot)
    sunny_totals, sunny_seconds, sunny_stock, sunny_diag = aggregate(sunny)
    keys = sorted(set(carrot_totals) | set(sunny_totals))
    rows = []
    for direction, message, bus in keys:
        ca = carrot_totals[(direction, message, bus)]
        su = sunny_totals[(direction, message, bus)]
        rows.append({
            "direction": direction, "message": message, "bus": bus,
            "carrot_frames": ca, "sunnypilot_frames": su,
            "carrot_frames_per_observed_second": round(ca / carrot_seconds, 3) if carrot_seconds else None,
            "sunnypilot_frames_per_observed_second": round(su / sunny_seconds, 3) if sunny_seconds else None,
        })
    return {
        "analysis": "passive_comparison_only",
        "carrot_segments": len(carrot.get("segments", [])),
        "sunnypilot_segments": len(sunny.get("segments", [])),
        "carrot_seconds_approx": carrot_seconds,
        "sunnypilot_seconds_approx": sunny_seconds,
        "can_comparison": rows,
        "recorded_stock_event_samples": {
            "carrot": dict(carrot_stock), "sunnypilot": dict(sunny_stock),
        },
        "diagnostic_requests": {
            "carrot": dict(carrot_diag), "sunnypilot": dict(sunny_diag),
        },
        "conclusion": "NOT AEB-VERIFIED. Frame counts and FCW flags cannot prove emergency braking.",
        "limits": "Rates use seconds with watched activity; missing segments and differing vehicle conditions limit comparisons. CAN bus 128/192 is not independent OEM ECU proof.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--carrot", required=True, type=Path)
    p.add_argument("--sunnypilot", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    carrot = json.loads(args.carrot.read_text(encoding="utf-8"))
    sunny = json.loads(args.sunnypilot.read_text(encoding="utf-8"))
    for name, report in (("carrot", carrot), ("sunnypilot", sunny)):
        if report.get("type") != "passive_fca_scc_timeline" or not report.get("segments"):
            p.error(f"{name}: expected nonempty passive_fca_scc_timeline JSON")
    result = compare(carrot, sunny)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Carrot segments:", result["carrot_segments"], "Sunnypilot segments:", result["sunnypilot_segments"])
    print("CAN groups:", len(result["can_comparison"]))
    print(result["conclusion"])
    print("Saved:", args.output)


if __name__ == "__main__":
    main()
