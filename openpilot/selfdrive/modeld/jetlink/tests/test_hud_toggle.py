"""Live HUD lifecycle and settings, without USB or a graphics context."""
from pathlib import Path
import base64
import json
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.selfdrive.modeld.jetlink import hud, hud_publisher


class HudToggleTests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.marker = Path(directory.name) / 'enabled'
    self.packet = Path(directory.name) / 'snapshot.packet'
    self.flags = {'IsOffroad': True}
    keys = set(re.findall(r'\{"([^"]+)"', (Path(__file__).resolve().parents[4] / 'common/params_keys.h').read_text()))
    def get_bool(key):
      if key not in keys:
        raise KeyError(key)
      return self.flags.get(key, False)
    self.params = SimpleNamespace(get_bool=get_bool)
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
    for flags in ({'IsOffroad': False}, {}):
      self.flags.clear()
      self.flags.update(flags)
      for enabled in (True, False):
        with self.subTest(flags=flags, enabled=enabled):
          self.marker.touch()
          with self.assertRaises(ValueError):
            hud.set_enabled(enabled, self.params)
          self.assertTrue(self.marker.exists())

  def test_params_backend_failure_is_a_handled_setting_error(self):
    params = SimpleNamespace(get_bool=Mock(side_effect=RuntimeError('backend failure')))
    for enabled in (True, False):
      with self.assertRaisesRegex(ValueError, 'offroad state'):
        hud.set_enabled(enabled, params)
    self.assertFalse(self.marker.exists())

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
    onroad_values = []

    def make_sources(services):
      sm = SimpleNamespace(valid=dict.fromkeys(services, False), alive=dict.fromkeys(services, False),
                           recv_time=dict.fromkeys(services, 0.), update=Mock())
      builds.append(sm)
      return sm

    def advance(_):
      steps.append(self.packet.exists())
      if self.packet.exists():
        record = json.loads(self.packet.read_bytes()[hud.HEADER.size:])
        onroad_values.append(base64.b64decode(record['params']['IsOnroad']))
      if len(steps) in (1, 3):
        self.marker.touch()
        self.flags['IsOffroad'] = len(steps) == 1
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
    self.assertEqual(onroad_values, [b'0', b'1'])
    self.assertEqual(len(builds), 2)
    for sm in builds:
      sm.update.assert_called_once_with(0)
    self.assertFalse(self.packet.exists())
    self.assertFalse(self.packet.with_suffix('.tmp').exists())


if __name__ == '__main__':
  unittest.main()
