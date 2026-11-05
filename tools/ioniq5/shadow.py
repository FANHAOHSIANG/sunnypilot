"""Open-loop HCOP comparison. No actuator output, ECU queries, or messaging publisher."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import math

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = spec_from_file_location("henry_curve", ROOT / "openpilot/selfdrive/controls/lib/curve_speed.py")
henry = module_from_spec(spec)
spec.loader.exec_module(henry)


class CurveShadow:
  def __init__(self):
    self.curve = henry.CurveV2()
    self.state = {}
    self.last_model = None
    self.block_until = 0.

  def feed(self, name, msg, stamp, valid=True):
    if name in ("carState", "carControl", "controlsState"):
      self.state[name] = (stamp, msg) if valid else (stamp, None)
      return None
    if name != "modelV2":
      return None
    if self.last_model is None or not 0 < stamp - self.last_model <= .15:
      self.curve = henry.CurveV2()
      self.block_until = 0.
    self.last_model = stamp
    state = {k: v for k, (t, v) in self.state.items() if 0 <= stamp-t <= .25 and v is not None}
    if not valid or len(state) != 3:
      self.curve.reset()
      return None
    cs = state["carState"]
    if str(msg.meta.laneChangeState) in ("laneChangeStarting", "laneChangeFinishing"):
      self.block_until = stamp + 2.
    if stamp < self.block_until or not state["carControl"].longActive or cs.gasPressed or cs.brakePressed:
      self.curve.reset()
      return None
    speed, curvature = float(cs.vEgo), float(state["controlsState"].curvature)
    x, vp, yaw = [np.asarray(v) for v in (msg.position.x, msg.velocity.x, msg.orientationRate.z)]
    if (not math.isfinite(speed) or speed < 0 or not math.isfinite(curvature)
        or any(v.shape != (33,) or not np.isfinite(v).all() for v in (x, vp, yaw))):
      self.curve.reset()
      return None
    cap = self.curve.update(x, yaw / np.maximum(vp, 1.), vp, speed, speed**2 * abs(curvature), curvature)
    return {"evaluated": True, "decel": cap < 0, "ceiling": float(cap) if np.isfinite(cap) else None}
