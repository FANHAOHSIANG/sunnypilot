"""Use the real Python Params API and key registry with a stub native backend.

No C4/libparams binary is available on this host. Keep check_key/get_bool and
UnknownKeyName real so unsupported keys fail exactly at the reported boundary.
"""
import ctypes
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.selfdrive.modeld.jetlink import hud
from tools.jetlink import hud_config


class HudParamsCompatibilityTests(unittest.TestCase):
  def setUp(self):
    common = Path(__file__).resolve().parents[4] / 'common'
    keys = set(re.findall(r'\{"([^"]+)"', (common / 'params_keys.h').read_text()))
    self.values = {'IsOffroad': True}
    native = Mock()
    native.params_last_error.return_value = None
    native.params_create.return_value = 1
    native.params_check_key.side_effect = lambda handle, key: key.decode() in keys
    native.params_get_bool.side_effect = lambda handle, key, block: self.values.get(key.decode(), False)
    spec = importlib.util.spec_from_file_location('hud_test_real_params', common / 'params.py')
    self.api = importlib.util.module_from_spec(spec)
    with patch.object(ctypes, 'CDLL', return_value=native), patch.dict(sys.modules, {
      'openpilot.common.swaglog': SimpleNamespace(cloudlog=Mock()),
    }):
      spec.loader.exec_module(self.api)
    self.params = self.api.Params()
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.marker = Path(directory.name) / 'hud-enabled'
    for module in (hud, hud_config):
      p = patch.object(module, 'ENABLE', self.marker)
      p.start()
      self.addCleanup(p.stop)

  def test_reported_unknown_key_reproduces_but_fixed_toggle_succeeds(self):
    with self.assertRaises(self.api.UnknownKeyName):
      self.params.get_bool('IsOnroad')
    hud.set_enabled(True, self.params)
    self.assertTrue(self.marker.exists())
    hud.set_enabled(False, self.params)
    self.assertFalse(self.marker.exists())
    for flags in ({'IsOffroad': False}, {}):
      self.values.clear()
      self.values.update(flags)
      with self.assertRaises(ValueError):
        hud.set_enabled(True, self.params)
      self.assertFalse(self.marker.exists())

  def test_cli_enable_disable_uses_registered_params(self):
    with patch.dict(sys.modules, {'openpilot.common.params': self.api}), patch('builtins.print'):
      for argument, expected in (('--enable', True), ('--disable', False)):
        with patch.object(sys, 'argv', ['hud_config.py', argument]):
          hud_config.main()
        self.assertEqual(self.marker.exists(), expected)


if __name__ == '__main__':
  unittest.main()
