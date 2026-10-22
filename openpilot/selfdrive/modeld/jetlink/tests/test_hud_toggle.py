"""Live HUD lifecycle and settings, without USB or a graphics context."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.selfdrive.modeld.jetlink import hud, hud_publisher


class FakeToggle:
  def __init__(self, text, value, initial_state, toggle_callback, **kwargs):
    self.checked = initial_state
    self.callback = toggle_callback

  def set_enabled(self, predicate):
    self.enabled = predicate

  def set_checked(self, value):
    self.checked = value

  def _update_state(self):
    pass


class HudToggleTests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.marker = Path(directory.name) / 'enabled'
    self.packet = Path(directory.name) / 'snapshot.packet'
    self.flags = {'IsOnroad': False, 'IsOffroad': True}
    self.params = SimpleNamespace(get_bool=lambda key: self.flags.get(key, False))
    for module in (hud, hud_publisher):
      for name, value in (('ENABLE', self.marker), ('PACKET', self.packet)):
        patcher = patch.object(module, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

  def test_persistent_opt_in_and_disable_are_shared(self):
    self.assertFalse(hud.supported({hud.CAPABILITY: True}))
    hud.set_enabled(True, self.params)
    self.assertTrue(hud.supported({hud.CAPABILITY: True}))
    self.assertFalse(hud.supported({hud.CAPABILITY: 1}))
    hud.set_enabled(False, self.params)
    hud.set_enabled(False, self.params)
    self.assertFalse(self.marker.exists())

  def test_unknown_or_onroad_state_rejects_both_changes(self):
    for onroad, offroad in ((True, True), (True, False), (False, False)):
      self.flags.update(IsOnroad=onroad, IsOffroad=offroad)
      for enabled in (True, False):
        with self.subTest(onroad=onroad, offroad=offroad, enabled=enabled):
          self.marker.touch()
          with self.assertRaises(ValueError):
            hud.set_enabled(enabled, self.params)
          self.assertTrue(self.marker.exists())

  def test_sender_observes_off_on_off_on_without_recreation(self):
    sender = hud.HudSender({}, path=self.packet)
    steps = []

    def advance(_):
      steps.append(sender.latest)
      if len(steps) == 1:
        self.packet.write_bytes(hud.HEADER.pack(10.) + b'first')
        hud.set_enabled(True, self.params)
      elif len(steps) == 2:
        hud.set_enabled(False, self.params)
      elif len(steps) == 3:
        self.packet.write_bytes(hud.HEADER.pack(10.1) + b'fresh')
        hud.set_enabled(True, self.params)
      else:
        sender.stop.set()

    with patch.object(sender.stop, 'wait', side_effect=advance), patch.object(hud.time, 'monotonic', return_value=10.15), \
         patch.object(hud.os, 'sched_setscheduler'), patch.object(hud.os, 'setpriority'):
      sender._read()
    self.assertEqual(steps, [None, (10., b'first'), None, (10.1, b'fresh')])

  def test_capable_connection_starts_watchers_even_when_disabled(self):
    with patch.object(hud, 'HudSender') as sender, patch.object(hud.subprocess, 'Popen') as process:
      with hud.HudSession({hud.CAPABILITY: True}) as active:
        self.assertIs(active, sender.return_value)
        process.assert_called_once()
        self.assertFalse(self.marker.exists())
      sender.return_value.close.assert_called_once()
      process.return_value.terminate.assert_called_once()
      process.return_value.wait.assert_called_once_with(timeout=1)

  def test_incapable_connection_never_starts_hud_workers(self):
    with patch.object(hud, 'HudSender') as sender, patch.object(hud.subprocess, 'Popen') as process:
      with hud.HudSession({}) as active:
        self.assertIsNone(active)
      sender.assert_not_called()
      process.assert_not_called()

  def test_publisher_unsubscribes_off_and_recreates_fresh_sources_on(self):
    builds = []
    steps = []

    def make_sources(services):
      sm = SimpleNamespace(valid=dict.fromkeys(services, False), alive=dict.fromkeys(services, False),
                           recv_time=dict.fromkeys(services, 0.), update=Mock())
      builds.append(sm)
      return sm

    def advance(_):
      steps.append(self.packet.exists())
      if len(steps) in (1, 3):
        self.marker.touch()
      elif len(steps) == 2:
        self.marker.unlink()
      else:
        raise KeyboardInterrupt

    modules = {'openpilot.cereal': SimpleNamespace(messaging=SimpleNamespace(SubMaster=make_sources)),
               'openpilot.common.params': SimpleNamespace(Params=lambda: self.params)}
    with patch.dict(sys.modules, modules), patch.object(hud_publisher.os, 'sched_setscheduler'), \
         patch.object(hud_publisher.os, 'nice'), patch.object(hud_publisher.time, 'sleep', side_effect=advance):
      with self.assertRaises(KeyboardInterrupt):
        hud_publisher.main()
    self.assertEqual(steps, [False, True, False, True])
    self.assertEqual(len(builds), 2)
    for sm in builds:
      sm.update.assert_called_once_with(0)
    self.assertFalse(self.packet.exists())
    self.assertFalse(self.packet.with_suffix('.tmp').exists())

  def make_toggle(self):
    state = SimpleNamespace(started=False, params=self.params)
    app = Mock()
    modules = {
      'openpilot.selfdrive.ui.mici.widgets.button': SimpleNamespace(BigToggle=FakeToggle),
      'openpilot.selfdrive.ui.mici.widgets.dialog': SimpleNamespace(BigDialog=lambda title, description: (title, description)),
      'openpilot.selfdrive.ui.ui_state': SimpleNamespace(ui_state=state),
      'openpilot.system.ui.lib.application': SimpleNamespace(gui_app=app),
      'openpilot.system.ui.lib.multilang': SimpleNamespace(tr=lambda value: value),
    }
    path = Path(__file__).resolve().parents[3] / 'ui/mici/widgets/jetlink_hud.py'
    spec = importlib.util.spec_from_file_location('hud_toggle_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
      spec.loader.exec_module(module)
    return module.JetlinkHudToggle(), state, app

  def test_ui_preserves_existing_setting_and_tracks_cli_changes(self):
    self.marker.touch()
    toggle, _, app = self.make_toggle()
    self.assertTrue(toggle.checked)
    self.assertTrue(toggle.enabled())
    toggle.callback(False)
    self.assertFalse(toggle.checked)
    self.assertFalse(self.marker.exists())
    self.assertEqual(app.push_widget.call_args.args[0][0], 'HUD setting saved')
    hud.set_enabled(True, self.params)
    toggle._update_state()
    self.assertTrue(toggle.checked)

  def test_ui_rechecks_started_and_recovers_from_write_failure(self):
    toggle, state, app = self.make_toggle()
    state.started = True
    self.assertFalse(toggle.enabled())
    toggle.callback(True)
    self.assertFalse(self.marker.exists())
    self.assertFalse(toggle.checked)
    self.assertEqual(app.push_widget.call_args.args[0][0], 'HUD setting unchanged')
    state.started = False
    with patch.object(hud, 'set_enabled', side_effect=OSError('read-only storage')):
      toggle.callback(True)
    self.assertFalse(toggle.checked)
    self.assertEqual(app.push_widget.call_args.args[0][0], 'HUD setting not saved')


if __name__ == '__main__':
  unittest.main()
