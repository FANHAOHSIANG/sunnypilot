#!/usr/bin/env python3
"""C4 read-only Hyundai FCA diagnostic snapshot. Does not access CAN TX or change Params."""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None, help="Optional local JSON output file")
    args = parser.parse_args()
    from openpilot.common.params import Params
    from opendbc.car import structs
    p = Params()
    raw = p.get("CarParams")
    if not raw:
        raise SystemExit("Missing CarParams; do not use this as evidence of ECU state")
    with structs.CarParams.from_bytes(raw) as cp:
        record = {
            "collected_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "carFingerprint": cp.carFingerprint,
            "flags": int(cp.flags),
            "openpilotLongitudinalControl": bool(cp.openpilotLongitudinalControl),
            "pcmCruise": bool(cp.pcmCruise),
            "radarUnavailable": bool(cp.radarUnavailable),
            "safetyConfigs": [
                {"model": str(c.safetyModel), "safetyParam": int(c.safetyParam)}
                for c in cp.safetyConfigs
            ],
            "warning": "Diagnostic snapshot only. Not evidence OEM AEB functions.",
        }
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Saved read-only snapshot to {args.output}")

if __name__ == "__main__":
    main()
