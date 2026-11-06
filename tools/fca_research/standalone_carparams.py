#!/usr/bin/env python3
"""Read-only local RLOG CarParams extraction without openpilot LogReader imports.

Requires only pycapnp and zstandard. Never imports openpilot hardware,
connects to the car, sends CAN, or changes vehicle settings.
"""
import argparse
import json
from pathlib import Path
import capnp
import zstandard as zstd


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rlog", type=Path)
    ap.add_argument("--checkout", type=Path, required=True, help="carrot-wip source root")
    ap.add_argument("--output", type=Path, default=Path("/tmp/carrot_carparams.json"))
    args = ap.parse_args()
    if not args.rlog.is_file():
        ap.error(f"RLOG missing: {args.rlog}")
    root = args.checkout.expanduser().resolve()
    log_schema = root / "openpilot/cereal/log.capnp"
    car_schema = root / "opendbc_repo/opendbc/car/car.capnp"
    if not log_schema.is_file() or not car_schema.is_file():
        ap.error(f"Missing log.capnp or car.capnp in {root}")
    # Absolute Cap'n Proto import "/car.capnp" resolves against include roots.
    # The include root must be the directory containing car.capnp.
    schema = capnp.load(str(log_schema), imports=[
        str(car_schema.parent),
        str(log_schema.parent),
    ])
    report = {"rlog": str(args.rlog), "carParamsEvents": [], "errors": 0}
    # Cap'n Proto messages are concatenated, each with segment table and 8-byte-aligned segments.
    # This scans one message at a time, without constructing a giant decoded buffer.
    with args.rlog.open("rb") as fh, zstd.ZstdDecompressor().stream_reader(fh) as reader:
        while True:
            header = reader.read(8)
            if not header:
                break
            if len(header) != 8:
                raise ValueError("Truncated capnp message header")
            nsegments = int.from_bytes(header[:4], "little") + 1
            if nsegments > 512:
                raise ValueError(f"Invalid segment count {nsegments}")
            table_size = ((nsegments + 2) // 2) * 8
            table = header[4:] + reader.read(table_size - 8)
            if len(table) != table_size - 4:
                raise ValueError("Truncated capnp segment table")
            words = [int.from_bytes(table[i*4:(i+1)*4], "little") for i in range(nsegments)]
            body_len = sum(words) * 8
            body = reader.read(body_len)
            if len(body) != body_len:
                raise ValueError("Truncated capnp message body")
            # pycapnp from_bytes() requires a complete framed message.
            try:
                with schema.Event.from_bytes(header[:4] + table + body, traversal_limit_in_words=2**26) as event:
                    if event.which() != "carParams":
                        continue
                    cp = event.carParams
                    report["carParamsEvents"].append({
                        "carFingerprint": str(cp.carFingerprint),
                        "flags": int(cp.flags),
                        "openpilotLongitudinalControl": bool(cp.openpilotLongitudinalControl),
                        "safetyConfigs": [{"model": str(x.safetyModel), "param": int(x.safetyParam)} for x in cp.safetyConfigs],
                        "logMonoTime": int(event.logMonoTime),
                    })
            except Exception:
                report["errors"] += 1
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print("CarParams events:", len(report["carParamsEvents"]))
    for x in report["carParamsEvents"][:5]:
        print(json.dumps(x, ensure_ascii=False))
    print("Decode errors:", report["errors"])
    print("Saved:", args.output)
    if not report["carParamsEvents"]:
        print("No carParams event in this segment; absence is not evidence of a missing vehicle flag.")


if __name__ == "__main__":
    main()
