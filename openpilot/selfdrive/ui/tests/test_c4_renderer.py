"""Execute C4's real engaged render path without opening a graphics window.

Extract methods to isolate raylib/capnp initialization, not to replace the
rendering logic. Reproduces the missing projection callback from C4 swaglogs.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import Mock

import numpy as np

from openpilot.selfdrive.ui.sunnypilot.onroad.blue_path import predicted_stop, ribbon_sections

ROOT = Path(__file__).resolve().parents[4]


def rectangle(x, y, width, height):
  return NS(x=x, y=y, width=width, height=height)


def load_class(path, name, namespace, methods=None):
  tree = ast.parse((ROOT / path).read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
  cls.bases = []
  if methods is not None:
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
  exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
  return namespace[name]


class C4RendererTests(unittest.TestCase):
  def setUp(self):
    self.rl = NS(Rectangle=rectangle, Color=lambda *args: args, Vector2=lambda *args: args, draw_line_ex=Mock())
    self.state = NS(status=1, started_frame=0, rainbow_path=False, rainbow_mode_style=1)
    status = NS(DISENGAGED=0, ENGAGED=1, LAT_ONLY=2)
    self.ns = dict(np=np, rl=self.rl, ui_state=self.state, UIStatus=status,
                   draw_polygon=Mock(), STOCK_PATH_GRADIENT=object(), LeadVehicle=lambda: None, CLIP_MARGIN=500)
    cls = load_class('openpilot/selfdrive/ui/mici/onroad/model_renderer.py', 'ModelRenderer', self.ns,
                     {'_render', '_draw_path', '_map_to_screen'})
    self.renderer = cls()
    r = self.renderer
    r._rect = rectangle(300, 50, 200, 200)
    r._clip_region = r._rect
    r._car_space_transform = np.diag([10., 10., 1.])
    r._counter = 1
    r._torque_filter = Mock()
    r._longitudinal_control = False
    r._transform_dirty = False
    r._draw_lane_lines = Mock()
    r._path = NS(projected_points=np.array([[20, 180], [20, 20], [180, 20], [180, 180]], np.float32))
    r.blue_path = Mock()
    r.blue_path.draw.return_value = True
    r.predicted_stop_marker = Mock()
    class Messages(dict):
      pass
    sm = Messages(carOutput=NS(actuatorsOutput=NS(torque=0.)),
                  extrinsicsCalibration=NS(height=[1.2]), modelV2=NS())
    sm.recv_frame = dict(extrinsicsCalibration=1, modelV2=1)
    sm.updated = dict(carParams=False, modelV2=False, radarState=False)
    sm.valid = dict(radarState=False)
    self.state.sm = sm

  def test_engaging_with_stop_marker_disabled_still_resolves_callback(self):
    # Original crash occurred while evaluating the callback argument, even
    # with the marker option off. Run that actual renderer branch.
    self.renderer._render(self.renderer._rect)
    call = self.renderer.predicted_stop_marker.render.call_args
    self.assertEqual(call.args[3](2., 3., 1.), (20., 30.))
    self.assertTrue(call.kwargs['local_coordinates'])

  def test_blue_path_and_stop_marker_work_in_engaged_and_mads_states(self):
    self.state.rainbow_path = True
    for status in (1, 2):
      self.state.status = status
      self.renderer._render(self.renderer._rect)
      call = self.renderer.blue_path.draw.call_args
      self.assertTrue(call.kwargs['local_coordinates'])
      self.assertEqual(call.args[4](2., 3., 1.), (20., 30.))

  def test_disengaged_resets_marker(self):
    self.state.status = 0
    self.renderer._render(self.renderer._rect)
    self.renderer.predicted_stop_marker.reset.assert_called_once()
    self.renderer.predicted_stop_marker.render.assert_not_called()

  def test_projection_uses_absolute_clip_but_returns_local_pixels(self):
    self.assertEqual(self.renderer._map_to_screen(2., 3., 1.), (20., 30.))
    self.assertIsNone(self.renderer._map_to_screen(30., 3., 1.))

  def test_degenerate_nonfinite_or_uninitialized_projection_is_hidden(self):
    for xyz in ((1., 1., 0.), (float('nan'), 1., 1.), (float('inf'), 1., 1.)):
      with self.subTest(xyz=xyz):
        self.assertIsNone(self.renderer._map_to_screen(*xyz))
    self.renderer._clip_region = None
    self.assertIsNone(self.renderer._map_to_screen(2., 3., 1.))

  def marker(self):
    return load_class('openpilot/selfdrive/ui/sunnypilot/onroad/predicted_stop_marker.py',
                      'PredictedStopMarker', dict(self.ns, time=time, predicted_stop=predicted_stop,
                                                 ribbon_sections=ribbon_sections))

  def test_stop_bar_local_and_absolute_callers_have_identical_screen_coordinates(self):
    cls = self.marker()
    r = self.renderer
    r._path.raw_points = np.array([[0., 0., 0.], [10., 0., 0.]])
    project = lambda x, y, z: (50. + y * 10., 100.)
    cls._draw_stop_bar(r._rect, r._path, 5., project, 0., local_coordinates=True)
    self.assertEqual(self.rl.draw_line_ex.call_count, 2)
    expected = self.rl.draw_line_ex.call_args_list.copy()
    self.assertEqual(expected[0].args[0], (341.5, 150.))
    self.rl.draw_line_ex.reset_mock()
    absolute_path = NS(raw_points=r._path.raw_points,
                       projected_points=r._path.projected_points + [r._rect.x, r._rect.y])
    absolute_project = lambda x, y, z: tuple(np.array(project(x, y, z)) + [r._rect.x, r._rect.y])
    cls._draw_stop_bar(r._rect, absolute_path, 5., absolute_project, 0.)
    self.assertEqual(self.rl.draw_line_ex.call_args_list, expected)


if __name__ == '__main__':
  unittest.main()
