"""Do not confuse USB power roles with data roles before gadget setup."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.modeld.jetlink import daemon


class UsbPresenceTests(unittest.TestCase):
  def test_configured_gadget_proves_host_even_without_typec_mode(self):
    self.check('', '', 'configured', True)

  def test_device_data_role_survives_power_role_swap(self):
    self.check('UFP', 'Sink attached', 'not attached', True)

  def test_host_data_role_does_not_pass_power_role_heuristic(self):
    self.check('DFP', 'Source attached', 'not attached', False)

  def test_no_cable_and_legacy_kernel(self):
    self.check('none', 'None', 'not attached', False)
    self.check('', 'Source attached (default)', 'not attached', True)
    self.check('', '', '', False)

  def check(self, role, power, state, expected):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      (root / 'udc/controller').mkdir(parents=True)
      (root / 'udc/controller/state').write_text(state)
      (root / 'role').write_text(role)
      (root / 'power').write_text(power)
      with patch.object(daemon, 'USB_DATA_ROLE', root / 'role'), patch.object(daemon, 'USB_TYPEC_MODE', root / 'power'), \
           patch.object(daemon, 'UDC_ROOT', root / 'udc'):
        self.assertEqual(daemon.host_attached(), expected)
