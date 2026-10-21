"""Persistent, display-only model predicted stop marker."""
import time

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.onroad.blue_path import predicted_stop, ribbon_sections
from openpilot.selfdrive.ui.ui_state import ui_state


class PredictedStopMarker:
  """Latch a predicted stop until the vehicle has stopped and starts moving again.

  Model stop intent can flicker for one or more frames. Once a valid stop distance is
  observed, keep a display-only estimate of the remaining distance instead of clearing
  immediately. This state never feeds planning or controls.
  """
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self._distance: float | None = None
    self._last_time: float | None = None
    self._saw_standstill = False

  def render(self, rect: rl.Rectangle, path, sm, project, z_offset: float, *, local_coordinates=False) -> None:
    if not getattr(ui_state, "predicted_stop_marker", False):
      self.reset()
      return
    now = time.monotonic()
    for service in ("modelV2", "carState"):
      if (not sm.valid[service] or not sm.alive[service] or
          sm.recv_frame[service] <= ui_state.started_frame or
          not 0 <= now - sm.recv_time[service] <= 0.5):
        self.reset()
        return
    car_state = sm["carState"]
    speed = max(0.0, float(car_state.vEgo))
    stopping, distance = predicted_stop(sm["modelV2"])

    dt = 0.0 if self._last_time is None else max(0.0, min(now - self._last_time, 0.25))
    self._last_time = now

    if stopping and distance is not None and np.isfinite(distance):
      self._distance = max(0.0, float(distance))
    elif self._distance is not None and not self._saw_standstill:
      # When stop intent flickers, keep the marker moving toward the vehicle using
      # measured ego speed instead of dropping it for one frame.
      self._distance = max(0.0, self._distance - speed * dt)

    if self._distance is None:
      return

    if speed < 0.25:
      self._saw_standstill = True
    elif self._saw_standstill and speed > 0.8:
      self.reset()
      return

    self._draw_stop_bar(rect, path, self._distance, project, z_offset, local_coordinates=local_coordinates)

  @staticmethod
  def _draw_stop_bar(rect: rl.Rectangle, path, distance: float, project, z_offset: float, *, local_coordinates=False) -> None:
    raw = path.raw_points
    if len(raw) < 2 or not np.isfinite(raw).all():
      return
    if not raw[0, 0] <= distance <= raw[-1, 0]:
      return

    y, z = (float(np.interp(distance, raw[:, 0], raw[:, k])) for k in (1, 2))
    projected = [project(distance, y + side, z + z_offset) for side in (-0.85, 0.85)]
    if any(p is None for p in projected):
      return

    sections = ribbon_sections(path.projected_points)
    if sections is None:
      return
    _, left, right = sections
    center_y = (projected[0][1] + projected[1][1]) / 2
    if not min(left[:, 1].min(), right[:, 1].min()) <= center_y <= max(left[:, 1].max(), right[:, 1].max()):
      return

    offset = np.array([rect.x, rect.y]) if local_coordinates else np.zeros(2)
    a, b = (np.asarray(p) + offset for p in projected)
    if not all(rect.x <= p[0] <= rect.x + rect.width and rect.y <= p[1] <= rect.y + rect.height for p in (a, b)):
      return

    thickness = max(4.0, rect.height * 0.007)
    glow = rl.Color(255, 160, 35, 85)
    line = rl.Color(255, 205, 90, 245)
    rl.draw_line_ex(rl.Vector2(*a), rl.Vector2(*b), thickness * 2.2, glow)
    rl.draw_line_ex(rl.Vector2(*a), rl.Vector2(*b), thickness, line)
