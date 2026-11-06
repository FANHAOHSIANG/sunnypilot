#!/usr/bin/env python3
"""Offline-only FCA/FCW source-path regression across pinned Carrot and Sunny forks.

Reads source files and validates control-path decisions using Python AST.
No vehicle I/O, ECU writes, CAN transmit, Panda modification or warning suppression.
"""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path

def _read(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")

def _class_expr(source, cls, member):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for n in node.body:
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == member for t in n.targets):
                    return ast.unparse(n.value)
    raise AssertionError(f"{cls}.{member} not found")

def _method(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node), [x.arg for x in node.args.args]
    raise AssertionError(f"Function {name} not found")

def inspect(carrot: Path, sunny: Path, carrot_flags: int, sunny_flags: int):
    cp = carrot / "opendbc_repo/opendbc/car/hyundai"
    sp = sunny / "opendbc_repo/opendbc/car/hyundai"
    cv, sv = _read(cp/"values.py"), _read(sp/"values.py")
    cc, sc = _read(cp/"carcontroller.py"), _read(sp/"carcontroller.py")
    cf, sf = _read(cp/"hyundaicanfd.py"), _read(sp/"hyundaicanfd.py")
    carrot_camera = _class_expr(cv, "HyundaiFlags", "CAMERA_SCC")
    sunny_camera = _class_expr(sv, "HyundaiFlags", "CAMERA_SCC")
    carrot_canfd = _class_expr(cv, "HyundaiFlags", "CANFD_CAMERA_SCC")
    sunny_canfd = _class_expr(sv, "HyundaiFlags", "CANFD_CAMERA_SCC")
    carrot_fn, carrot_args = _method(cf, "create_fca_warning_light")
    sunny_fn, sunny_args = _method(sf, "create_fca_warning_light")
    carrot_ctrl, _ = _method(cc, "update")
    sunny_ctrl, _ = _method(sc, "create_canfd_msgs")
    # Explicit source checks: fail closed when upstream changes.
    assert carrot_canfd in ("2 ** 3", "8"), carrot_canfd
    assert sunny_canfd in ("2 ** 3", "8"), sunny_canfd
    assert carrot_camera == "CANFD_CAMERA_SCC", carrot_camera
    assert sunny_camera in ("2 ** 15", "32768"), sunny_camera
    assert carrot_flags & 8 and sunny_flags & 8
    assert "CP" in carrot_args and "CP" not in sunny_args
    assert "if CP.flags & HyundaiFlags.CAMERA_SCC" in carrot_fn
    assert "return ret" in carrot_fn
    assert "'AEB_SETTING': 1" in carrot_fn and "'AEB_SETTING': 1" in sunny_fn
    assert "create_fca_warning_light(self.CP," in carrot_ctrl
    assert "create_fca_warning_light(self.packer," in sunny_ctrl
    assert "if lka_steering:" in sunny_ctrl
    assert "create_adrv_messages" in sunny_ctrl
    return {
        "status": "PASS_SOURCE_PATH_ONLY",
        "carrot_flags": carrot_flags, "sunny_flags": sunny_flags,
        "carrot_camera_scc_bit": 3, "sunny_camera_scc_bit": 15,
        "carrot_camera_scc_enabled": bool(carrot_flags & 8),
        "sunny_canfd_camera_scc_enabled": bool(sunny_flags & 8),
        "sunny_distinct_camera_scc_enabled": bool(sunny_flags & (1<<15)),
        "carrot_fca_warning_path": "suppressed_by_CAMERA_SCC_alias",
        "sunny_fca_warning_path": "called_for_non_LKA_steering_longitudinal",
        "warning": "This is a source-level control-path assertion, NOT an AEB/FCW functional or vehicle safety validation."
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--carrot", type=Path, required=True)
    ap.add_argument("--sunny", type=Path, required=True)
    ap.add_argument("--carrot-flags", type=int, default=402663433)
    ap.add_argument("--sunny-flags", type=int, default=134227976)
    ap.add_argument("--output", type=Path, default=Path("/tmp/fca_source_path_report.json"))
    a = ap.parse_args()
    result = inspect(a.carrot.expanduser(), a.sunny.expanduser(), a.carrot_flags, a.sunny_flags)
    a.output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
