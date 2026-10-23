"""Exercise production C4 scrolling, labels and input without a GPU/device.

Run separately: PYTHONPATH=.:third_party/jetlink python -m unittest discover
  -s tools/jetlink/tests -p test_hud_ui.py -v
Requires pyray. Only graphics/assets, clocks, Params and vehicle IPC are mocked;
Widget, BigToggle, UnifiedLabel, NavScroller and touch recognition are real.
This is not an ARM/GPU rendering or physical touchscreen test.
"""
import importlib
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import Mock, patch

import pyray as rl
import numpy  # noqa: F401; keep the native extension loaded while isolating UI modules.

from openpilot.selfdrive.modeld.jetlink import hud


class MousePos(NamedTuple):
  x: float
  y: float


class MouseEvent(NamedTuple):
  pos: MousePos
  slot: int
  left_pressed: bool
  left_released: bool
  left_down: bool
  t: float


class Params:
  keys = set(re.findall(r'\{"([^"]+)"', (Path(__file__).resolve().parents[3] / 'openpilot/common/params_keys.h').read_text()))

  def __init__(self):
    self.values = {'IsOffroad': True}

  def get_bool(self, key, *args):
    if key not in self.keys:
      raise KeyError(key)
    return bool(self.values.get(key, False))

  def get(self, key, *args, **kwargs):
    return self.values.get(key, 0)

  def put_bool(self, key, value, **kwargs):
    self.values[key] = value


class HudNativeUITests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.marker = Path(directory.name) / 'hud-enabled'
    self.patch(hud, 'ENABLE', self.marker)
    self.clock = 10.
    self.patch(time, 'monotonic', lambda: self.clock)
    self.params = Params()
    self.app = SimpleNamespace(target_fps=60, height=540, width=1080, frame=0, show_touches=False,
                               mouse_events=[], last_mouse_event=None,
                               font=lambda _: SimpleNamespace(texture=SimpleNamespace(id=1)),
                               texture=lambda path, w, h: rl.Texture(1, w, h, 1, 7),
                               push_widget=Mock(side_effect=AssertionError('unexpected dialog')),
                               pop_widget=Mock(), set_show_touches=Mock(), set_show_fps=Mock())
    self.state = SimpleNamespace(params=self.params, started=False, engaged=False, CP=None,
                                 has_longitudinal_control=False, personality=0,
                                 sm=SimpleNamespace(updated={'selfdriveState': False}),
                                 update_params=Mock(), add_engaged_transition_callback=Mock())
    weights = SimpleNamespace(NORMAL=0, BOLD=1, ROMAN=2, DISPLAY_REGULAR=3, MEDIUM=4)
    application = SimpleNamespace(gui_app=self.app, MousePos=MousePos, MouseEvent=MouseEvent, MAX_TOUCH_SLOTS=2,
                                  FontWeight=weights, DEFAULT_TEXT_SIZE=48, DEFAULT_TEXT_COLOR=rl.WHITE,
                                  FONT_SCALE=.9, TextAlignment=SimpleNamespace(LEFT=0, CENTER=1, RIGHT=2),
                                  TextAlignmentVertical=SimpleNamespace(TOP=0, BOTTOM=1, MIDDLE=2),
                                  font_fallback=lambda font, text='': font, text_size_scale=lambda text: 1.)
    modules = {
      'openpilot.system.ui.lib.application': application,
      'openpilot.system.ui.lib.multilang': SimpleNamespace(tr=lambda text: text),
      'openpilot.common.swaglog': SimpleNamespace(cloudlog=Mock()),
      'openpilot.common.params': SimpleNamespace(Params=lambda: self.params),
      'openpilot.common.hardware': SimpleNamespace(COMMA_HARDWARE=True),
      'openpilot.cereal': SimpleNamespace(log=SimpleNamespace(LongitudinalPersonality=SimpleNamespace(
        schema=SimpleNamespace(enumerants={'standard': 0})))),
      'openpilot.selfdrive.ui.ui_state': SimpleNamespace(ui_state=self.state, device=SimpleNamespace(awake=True)),
      'openpilot.selfdrive.ui.layouts.settings.common': SimpleNamespace(restart_needed_callback=Mock()),
      'openpilot.selfdrive.ui.mici.widgets.dialog': SimpleNamespace(SettingDescriptionDialog=Mock()),
    }
    # Each test imports fresh production UI classes against its own host facade.
    prefixes = ('openpilot.system.ui.widgets', 'openpilot.system.ui.lib.scroll_panel2',
                'openpilot.system.ui.lib.text_measure', 'openpilot.system.ui.lib.wrap_text',
                'openpilot.selfdrive.ui.mici.widgets.button', 'openpilot.selfdrive.ui.mici.layouts.settings.toggles')
    saved = {k: v for k, v in sys.modules.items() if k.startswith(prefixes)}
    for key in saved:
      del sys.modules[key]
    def restore():
      for key in list(sys.modules):
        if key.startswith(prefixes):
          del sys.modules[key]
      sys.modules.update(saved)
    self.addCleanup(restore)
    module_patch = patch.dict(sys.modules, modules)
    module_patch.start()
    self.addCleanup(module_patch.stop)
    self.patch(rl, 'get_time', lambda: self.clock)
    self.patch(rl, 'get_frame_time', lambda: 1 / 60)
    self.patch(rl, 'measure_text_ex', lambda font, text, size, spacing: rl.Vector2(len(text) * size * .5, size * .9))
    for name in dir(rl):
      if name.startswith('draw_') or name in ('begin_scissor_mode', 'end_scissor_mode'):
        self.patch(rl, name, Mock())
    self.draw_text = rl.draw_text_ex
    self.button = importlib.import_module('openpilot.selfdrive.ui.mici.widgets.button')
    class InitialCruise(self.button.BigToggle):
      def __init__(self):
        super().__init__('initial cruise')
      def refresh(self):
        pass
    module_patch2 = patch.dict(sys.modules, {
      'openpilot.selfdrive.ui.mici.layouts.settings.initial_cruise_speed': SimpleNamespace(InitialCruiseSpeedButton=InitialCruise)
    })
    module_patch2.start()
    self.addCleanup(module_patch2.stop)
    layout_module = importlib.import_module('openpilot.selfdrive.ui.mici.layouts.settings.toggles')
    self.layout = layout_module.TogglesLayoutMici()
    self.toggle = self.layout._hud_toggle
    self.layout.show_event()
    self.layout._y_pos_filter.x = 0.
    self.layout._y_pos_filter.velocity.x = 0.
    self.frame()

  def patch(self, obj, name, value):
    p = patch.object(obj, name, value)
    p.start()
    self.addCleanup(p.stop)

  def frame(self, event=None):
    self.clock += 1 / 60
    self.app.frame += 1
    self.app.mouse_events = [] if event is None else [event]
    if event is not None:
      self.app.last_mouse_event = event
    self.layout.render(rl.Rectangle(0, 0, 1080, 540))

  def event(self, x, y=350, pressed=False, released=False):
    return MouseEvent(MousePos(x, y), 0, pressed, released, not released, self.clock + 1 / 60)

  def focus_hud(self):
    scroller = self.layout._scroller
    offset = scroller.scroll_panel.get_offset() + 540 - self.toggle.rect.x - self.toggle.rect.width / 2
    scroller.scroll_panel.set_offset(offset)
    self.frame()

  def tap(self):
    self.focus_hud()
    x = self.toggle.rect.x + 180
    self.frame(self.event(x, pressed=True))
    self.frame(self.event(x, released=True))
    self.frame()

  def test_native_hud_draws_across_viewport_without_settings_io(self):
    self.assertIs(type(self.toggle), self.button.BigToggle)
    self.assertEqual(self.toggle.value, '')
    with patch.object(hud, 'ENABLE') as marker, patch.object(hud, 'set_enabled') as write:
      marker.is_file.side_effect = AssertionError('file access from render')
      for offset in list(range(0, -2600, -20)) + list(range(-2600, 0, 20)):
        self.layout._scroller.scroll_panel.set_offset(offset)
        self.frame()
      write.assert_not_called()
    self.assertTrue(any(call.args[1] == 'HUD' for call in self.draw_text.call_args_list))
    self.app.push_widget.assert_not_called()

  def test_swipe_across_hud_never_toggles_it(self):
    self.focus_hud()
    with patch.object(hud, 'set_enabled') as write:
      self.frame(self.event(700, pressed=True))
      for x in range(680, 180, -20):
        self.frame(self.event(x))
      self.frame(self.event(180, released=True))
      for _ in range(120):
        self.frame()
      write.assert_not_called()
    self.assertFalse(self.marker.exists())

  def test_taps_toggle_live_and_reopening_reflects_cli(self):
    self.tap()
    self.assertTrue(self.marker.exists())
    self.assertTrue(self.toggle._checked)
    self.tap()
    self.assertFalse(self.marker.exists())
    hud.set_enabled(True, self.params)
    self.layout.show_event()
    self.assertTrue(self.toggle._checked)
    self.app.push_widget.assert_not_called()

  def test_onroad_race_and_storage_error_do_not_crash_or_change_setting(self):
    self.state.started = True
    self.tap()
    self.assertFalse(self.marker.exists())
    self.layout._on_hud_toggle(True)
    self.assertFalse(self.toggle._checked)
    self.state.started = False
    with patch.object(hud, 'set_enabled', side_effect=OSError('read-only')) as write:
      self.tap()
      write.assert_called_once_with(True, self.params)
    self.assertFalse(self.toggle._checked)
    self.assertEqual(self.toggle.text, 'HUD: error')
    self.frame()

  def test_unreadable_setting_disables_button_and_recovers_on_reopen(self):
    with patch.object(hud, 'ENABLE') as marker:
      marker.is_file.side_effect = OSError('unreadable')
      self.layout.show_event()
      self.assertFalse(self.toggle.enabled)
      self.frame()
    self.layout.show_event()
    self.assertTrue(self.toggle.enabled)
    for _ in range(120):
      self.frame()
    self.tap()
    self.assertTrue(self.marker.exists())
    self.assertEqual(self.toggle.text, 'HUD')

  def test_params_failure_during_click_keeps_ui_alive_and_setting_off(self):
    with patch.object(self.params, 'get_bool', side_effect=RuntimeError('backend failure')):
      self.tap()
    self.assertFalse(self.marker.exists())
    self.assertFalse(self.toggle._checked)
    self.assertEqual(self.toggle.text, 'HUD: error')
    self.frame()


if __name__ == '__main__':
  unittest.main()
