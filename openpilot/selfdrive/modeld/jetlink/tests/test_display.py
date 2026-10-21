"""Ready notifications and actual C4 HUD code, without a GL context."""
import ast
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.common import jetlink_status as status

ROOT = Path(__file__).resolve().parents[5]


def load_methods(path, name, methods, env):
  cls = next(n for n in ast.parse((ROOT / path).read_text()).body if isinstance(n, ast.ClassDef) and n.name == name)
  cls.bases = []
  cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
  exec(compile(ast.Module(body=[cls], type_ignores=[]), path, 'exec'), env)
  return env[name]


class DisplayTests(unittest.TestCase):
  def setUp(self):
    self.sm = {'modelV2': NS(big=True)}
    class Messages(dict): pass
    self.sm = Messages(self.sm)
    self.sm.seen = self.sm.alive = self.sm.valid = {'modelV2': True}
    self.sm.recv_time = {'modelV2': 10.}
    self.model = dict(updated=10., active=True, state='active', proving=False, sha256='a' * 64, handover_at=9.)
    self.link = dict(updated=10., state='ready', sha256='a' * 64)
    def read(path=status.STATUS, now=None):
      value = self.model if path == status.MODEL_STATUS else self.link
      return value if 0 <= now - value.get('updated', -100) < 3 else {'state': 'unavailable'}
    for p in (patch.object(status, 'read_status', side_effect=read), patch.object(status, 'fault_active', return_value=False)):
      p.start()
      self.addCleanup(p.stop)

  def display(self, onroad=True, now=10.1):
    return status.display_state(self.sm, onroad, now=now)

  def test_green_requires_active_proven_matching_live_model(self):
    self.assertEqual(self.display(), 'active')
    self.model['proving'] = True
    self.assertEqual(self.display(), 'loading')
    self.model['proving'] = False
    for field, value in (('active', False), ('sha256', 'b' * 64), ('state', 'local')):
      original = self.model[field]
      self.model[field] = value
      self.assertEqual(self.display(), 'ready')
      self.model[field] = original

  def test_offroad_engine_ready_and_stale_model_never_mean_active(self):
    self.assertEqual(self.display(onroad=False), 'ready')
    self.model['updated'] = 6.
    self.assertEqual(self.display(), 'ready')
    self.link['updated'] = 6.
    self.assertEqual(self.display(), 'disconnected')

  def test_live_model_stale_invalid_missing_or_not_big_never_green(self):
    self.sm.recv_time['modelV2'] = 9.
    self.assertEqual(self.display(), 'ready')
    self.sm.recv_time['modelV2'] = 10.
    self.sm.valid['modelV2'] = False
    self.assertEqual(self.display(), 'ready')
    self.sm.valid['modelV2'] = True
    self.sm['modelV2'].big = False
    self.assertEqual(self.display(), 'ready')

  def test_fatal_fault_overrides_even_a_proven_model(self):
    with patch.object(status, 'fault_active', return_value=True):
      self.assertEqual(self.display(), 'failed')

  def test_notice_fires_once_after_proof_and_again_only_for_new_admission(self):
    notice = status.ReadyNotice()
    self.model['proving'] = True
    self.assertFalse(notice.update(self.sm, True, now=10.1))
    self.model['proving'] = False
    self.assertTrue(notice.update(self.sm, True, now=10.1))
    self.assertFalse(notice.update(self.sm, True, now=10.1))
    self.model['updated'] = 6.
    self.assertFalse(notice.update(self.sm, True, now=10.1))
    self.model['updated'] = 10.
    self.assertFalse(notice.update(self.sm, True, now=10.1))
    self.model['handover_at'] = 10.
    self.assertTrue(notice.update(self.sm, True, now=10.1))

  def test_disabled_or_unproven_link_never_announces_ready(self):
    notice = status.ReadyNotice()
    self.assertFalse(notice.update(self.sm, False, now=10.1))
    self.model['active'] = False
    self.assertFalse(notice.update(self.sm, True, now=10.1))

  def test_actual_ui_method_uses_display_state_without_faking_physical_detection(self):
    states = Enum('ChestnutState', {'ACTIVE':'active', 'READY':'ready'})
    cls = load_methods('openpilot/selfdrive/ui/ui_state.py', 'UIState', {'_update_chestnut_state'},
                       dict(ChestnutState=states, jetlink_display_state=lambda sm, started: 'active'))
    ui = cls()
    ui.jetlink_enabled, ui.started, ui.sm, ui.chestnut_present = True, True, self.sm, False
    ui._update_chestnut_state()
    self.assertEqual(ui.chestnut_state, states.ACTIVE)
    self.assertFalse(ui.chestnut_present)

  def test_actual_hud_keeps_jetlink_icon_visible_and_uses_white_until_active(self):
    states = NS(LOADING='loading', READY='ready', ACTIVE='active', UNCOMPILED='uncompiled', FAILED='failed')
    ui = NS(chestnut_state='ready', jetlink_enabled=True)
    draw = Mock()
    rl = NS(Rectangle=object, get_time=lambda:100., Vector2=lambda *v:v, Color=lambda *v:v, draw_texture_ex=draw)
    cls = load_methods('openpilot/selfdrive/ui/mici/onroad/hud_renderer.py', 'HudRenderer', {'_draw_model_source'},
                       dict(rl=rl, ui_state=ui, ChestnutState=states, SET_SPEED_PERSISTENCE=5.))
    hud = cls()
    hud._txt_chestnut, hud._txt_chestnut_green, hud._txt_chestnut_orange = (NS(width=60, height=44) for _ in range(3))
    hud._txt_wheel = NS(height=44)
    hud._chestnut_icon, hud._chestnut_fade_time = hud._txt_chestnut, 0.
    hud._chestnut_alpha_filter = NS(update=Mock(return_value=1.))
    rect = NS(x=0, y=0, width=1000, height=500)
    hud._draw_model_source(rect)
    hud._chestnut_alpha_filter.update.assert_called_with(True)
    self.assertIs(draw.call_args.args[0], hud._txt_chestnut)
    ui.chestnut_state = 'active'
    hud._draw_model_source(rect)
    self.assertIs(draw.call_args.args[0], hud._txt_chestnut_green)


if __name__ == '__main__':
  unittest.main()
