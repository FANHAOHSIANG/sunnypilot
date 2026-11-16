#!/usr/bin/env python3
"""Materialize a BENCH-ONLY paired controller/safety candidate in a NEW directory.

Does not alter the input checkout, any git ref, Params, device or firmware.
Activation is absent from normal interface/UI. Production safety builds refuse
this new parameter; HYUNDAI_FCW_BENCH is added only to the libsafety TEST loader.
"""
from __future__ import annotations
import argparse
import ast
import difflib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

BASE_SHA = "1c51731784729da5ebaf24b3c34106bd27aaf94b"
HERE = Path(__file__).resolve().parent


def replace_once(text: str, old: str, new: str, description: str) -> str:
  count = text.count(old)
  if count != 1:
    raise ValueError(f"{description}: expected one exact source anchor, found {count}")
  return text.replace(old, new, 1)


def modifications(files: dict[str, str]) -> dict[str, str]:
  out = dict(files)
  p = "opendbc/car/hyundai/values.py"
  out[p] = replace_once(out[p], "  CANFD_DYNAMIC_TORQUE = 1024\n",
    "  CANFD_DYNAMIC_TORQUE = 1024\n  CANFD_FCA_BENCH = 2048  # NO VEHICLE ACTIVATION: paired bench configuration only\n", p)
  out[p] = replace_once(out[p], "  CANFD_DYNAMIC_TORQUE = 2 ** 27\n",
    "  CANFD_DYNAMIC_TORQUE = 2 ** 27\n  CANFD_FCA_BENCH = 2 ** 28  # Not enabled by interface or UI\n", p)

  p = "opendbc/can/parser.py"
  out[p] = replace_once(out[p], "  last_warning_log_nanos: int = 0\n",
    "  last_warning_log_nanos: int = 0\n  last_valid_raw: bytes = b\"\"\n  last_valid_raw_nanos: int = 0\n", p)
  out[p] = replace_once(out[p], "    self.timestamps.append(nanos)\n",
    "    # Read-only raw snapshot: retain all OEM bits, including unknown fields.\n"
    "    self.last_valid_raw = bytes(dat) if len(dat) == self.size else b\"\"\n"
    "    self.last_valid_raw_nanos = nanos\n"
    "    self.timestamps.append(nanos)\n", p)

  p = "opendbc/car/hyundai/carstate.py"
  out[p] = replace_once(out[p], "    self.cruise_info = {}\n",
    "    self.cruise_info = {}\n    self.fca_source_data = None\n    self.fca_source_ns = 0\n", p)
  anchor = ("  def update_canfd(self, can_parsers) -> tuple[structs.CarState, structs.CarStateSP]:\n"
            "    cp = can_parsers[Bus.pt]\n    cp_cam = can_parsers[Bus.cam]\n")
  out[p] = replace_once(out[p], anchor, anchor +
    "    if self.CP.flags & HyundaiFlags.CANFD_FCA_BENCH:\n"
    "      state = cp_cam.message_states.get(0x160)\n"
    "      self.fca_source_data = None if state is None else state.last_valid_raw\n"
    "      self.fca_source_ns = 0 if state is None else state.last_valid_raw_nanos\n", p)
  out[p] = replace_once(out[p], 'Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CanBus(CP).CAM),',
    'Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [("ADRV_0x160", 50)] if CP.flags & HyundaiFlags.CANFD_FCA_BENCH else [], CanBus(CP).CAM),', p)

  p = "opendbc/car/hyundai/carcontroller.py"
  out[p] = 'from opendbc.car.hyundai.fca_proxy import FcaProxy, validate_bench_cp\n' + out[p]
  out[p] = replace_once(out[p], "    self.CAN = CanBus(CP)\n",
    "    self.CAN = CanBus(CP)\n    validate_bench_cp(CP)\n    self.fca_proxy = FcaProxy()\n    self.fca_now_ns = 0\n", p)
  out[p] = replace_once(out[p], "    EsccCarController.update(self, CS)\n",
    "    EsccCarController.update(self, CS)\n    self.fca_now_ns = now_nanos\n", p)
  anchor = "      else:\n        can_sends.extend(hyundaicanfd.create_fca_warning_light(self.packer, self.CAN, self.frame))\n"
  replacement = ("      elif self.CP.flags & HyundaiFlags.CANFD_FCA_BENCH:\n"
    "        # The Panda candidate accepts only a fresh, exact OEM frame it observed.\n"
    "        source_msg = self.fca_proxy.forward(CS.fca_source_data, CS.fca_source_ns, self.fca_now_ns, self.CAN.CAM, self.CAN.ECAN)\n"
    "        if source_msg is not None:\n          can_sends.append(source_msg)\n"
    "      else:\n        can_sends.extend(hyundaicanfd.create_fca_warning_light(self.packer, self.CAN, self.frame))\n")
  out[p] = replace_once(out[p], anchor, replacement, p)

  p = "opendbc/safety/modes/hyundai_canfd.h"
  out[p] = replace_once(out[p], '#include "opendbc/safety/modes/hyundai_common.h"\n',
    '#include "opendbc/safety/modes/hyundai_common.h"\n#include "opendbc/safety/modes/hyundai_fca_proxy.h"\n\nstatic HyundaiFcaProxy hyundai_fca_proxy;\n', p)
  out[p] = replace_once(out[p], "static void hyundai_canfd_rx_hook(const CANPacket_t *msg) {\n",
    "static void hyundai_canfd_rx_hook(const CANPacket_t *msg) {\n"
    "  if (hyundai_fca_proxy.enabled && (msg->addr == HFCA_ADDRESS) && (msg->bus == HFCA_SOURCE_BUS)) {\n"
    "    const uint32_t now = microsecond_timer_get();\n"
    "    (void)hfca_observe(&hyundai_fca_proxy, msg->addr, msg->bus, msg->data, GET_LEN(msg), now);\n"
    "    if (!hfca_fcw_ready(&hyundai_fca_proxy, now)) {\n      controls_allowed = false;\n    }\n"
    "    return;\n  }\n", p)
  out[p] = replace_once(out[p], "static bool hyundai_canfd_tx_hook(const CANPacket_t *msg) {\n",
    "static bool hyundai_canfd_tx_hook(const CANPacket_t *msg) {\n"
    "  if (hyundai_fca_proxy.enabled) {\n"
    "    const uint32_t now = microsecond_timer_get();\n"
    "    if (!hfca_fcw_ready(&hyundai_fca_proxy, now)) {\n      controls_allowed = false;\n    }\n"
    "    if (msg->addr == HFCA_ADDRESS) {\n"
    "      return hfca_authorize_tx(&hyundai_fca_proxy, msg->addr, msg->bus, msg->data, GET_LEN(msg), now);\n"
    "    }\n  }\n", p)
  out[p] = replace_once(out[p], "static safety_config hyundai_canfd_init(uint16_t param) {\n",
    "static safety_config hyundai_canfd_init(uint16_t param) {\n"
    "  const bool fca_requested = (param & HFCA_BENCH_PARAM) != 0U;\n"
    "  bool fca_enabled = false;\n"
    "#ifdef HYUNDAI_FCW_BENCH\n"
    "  fca_enabled = param == (HFCA_SUPPORTED_BASE_PARAM | HFCA_BENCH_PARAM);\n"
    "#endif\n  hfca_reset(&hyundai_fca_proxy, fca_enabled);\n", p)
  out[p] = replace_once(out[p], "  return ret;\n}\n\nconst safety_hooks hyundai_canfd_hooks",
    "  if (fca_enabled) {\n"
    "    static RxCheck fca_bench_rx_checks[] = {\n"
    "      HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(0)\n"
    "      {.msg = {{0x160, 2, 16, 50U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},\n"
    "    };\n    SET_RX_CHECKS(fca_bench_rx_checks, ret);\n  }\n"
    "  if (fca_requested && !fca_enabled) {\n"
    "    // A production build or unsupported combination must never partially activate.\n"
    "    ret = (safety_config){.disable_forwarding = true};\n  }\n"
    "  return ret;\n}\n\n"
    "static uint32_t hyundai_fca_candidate_checksum(const CANPacket_t *msg) {\n"
    "  if (hyundai_fca_proxy.enabled && (msg->addr == HFCA_ADDRESS) && (GET_LEN(msg) == HFCA_LENGTH)) {\n"
    "    return hfca_crc(msg->data);\n  }\n"
    "  return hyundai_common_canfd_compute_checksum(msg);\n}\n\nconst safety_hooks hyundai_canfd_hooks", p)
  out[p] = replace_once(out[p], ".compute_checksum = hyundai_common_canfd_compute_checksum,",
    ".compute_checksum = hyundai_fca_candidate_checksum,", p)
  # Do NOT change any TX list, relay checks, forwarding rule or acceleration limit.
  for field in ("HYUNDAI_CANFD_LFA_STEERING_CAMERA_SCC_TX_MSGS", "longitudinal_accel_checks", "check_relay"):
    if out[p].count(field) != files[p].count(field):
      raise ValueError("Unexpected mutation of existing safety contract: " + field)

  out["opendbc/car/hyundai/fca_proxy.py"] = (HERE / "candidate_src/fca_proxy.py").read_text()
  out["opendbc/safety/modes/hyundai_fca_proxy.h"] = (HERE / "candidate_src/hyundai_fca_proxy.h").read_text()
  return out


PATHS = ["opendbc/car/hyundai/values.py", "opendbc/can/parser.py", "opendbc/car/hyundai/carstate.py",
         "opendbc/car/hyundai/carcontroller.py", "opendbc/safety/modes/hyundai_canfd.h"]


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument("--source", type=Path, required=True)
  p.add_argument("--output", type=Path, required=True)
  a = p.parse_args()
  src, dst = a.source.resolve(), a.output.resolve()
  if str(src).startswith("/data/") or str(dst).startswith("/data/"):
    p.error("Not permitted on comma device paths")
  if dst.exists() or dst == src or src in dst.parents:
    p.error("Output must be a NEW, separate directory, not inside the input checkout")
  head = subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
  if head != BASE_SHA:
    p.error(f"Require exact reviewed source {BASE_SHA}; got {head}")
  # Export the committed tree, not the possibly dirty user's working tree.
  archive = subprocess.check_output(["git", "-C", str(src), "archive", "--format=tar", BASE_SHA])
  originals = {path: subprocess.check_output(["git", "-C", str(src), "show", f"{BASE_SHA}:{path}"], text=True) for path in PATHS}
  changed = modifications(originals)
  for path, data in changed.items():
    if path.endswith(".py"):
      ast.parse(data, filename=path)
  dst.mkdir(parents=True)
  with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tf:
    for member in tf.getmembers():
      target = (dst / member.name).resolve()
      if not target.is_relative_to(dst) or member.issym() or member.islnk():
        raise ValueError("Unsafe archive member: " + member.name)
    tf.extractall(dst, filter="data")
  patch = []
  for path, data in changed.items():
    target = dst / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data)
    before = originals.get(path, "")
    patch.extend(difflib.unified_diff(before.splitlines(True), data.splitlines(True),
      fromfile="a/"+path if path in originals else "/dev/null", tofile="b/"+path))
  (dst / "FCW_CANDIDATE.patch").write_text("".join(patch))
  (dst / "FCW_CANDIDATE.json").write_text(json.dumps({
    "baseline": BASE_SHA, "status": "BENCH_ONLY_NOT_ROAD_VALIDATED",
    "default_enabled": False, "hardware_activation": "REFUSED_BY_PRODUCTION_SAFETY_BUILD",
    "changed_files": {k: hashlib.sha256(v.encode()).hexdigest() for k,v in changed.items()},
    "preserved": ["SCC_CONTROL isolation", "TCS denylist", "relay checks", "acceleration limits", "steering limits"],
  }, indent=2)+"\n")
  print("Paired candidate created:", dst)
  print("Original source not changed; no interface/UI activation; not an installable road release.")

if __name__ == "__main__":
  main()
