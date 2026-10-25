import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.selfdrive.modeld.jetlink.hud_radar_live import LiveRadar
from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import PassiveRadar, apply_trial


class TestLiveRadar(unittest.TestCase):
  def test_reader_owned_lifecycle(self):
    received = threading.Event()
    def recv(sock):
      received.set()
      time.sleep(.005)
      return None
    messaging = SimpleNamespace(sub_sock=lambda *a, **k: object(), recv_one=recv)
    with patch.dict('sys.modules', {'openpilot.cereal': SimpleNamespace(messaging=messaging)}), \
         patch('openpilot.selfdrive.modeld.jetlink.hud_radar_live.installed_bus', return_value=1):
      radar = LiveRadar()
      try:
        self.assertTrue(received.wait(1))
        self.assertEqual(radar.snapshot(time.monotonic(), 'boot')['points'], [])
      finally:
        radar.close()
      self.assertFalse(radar.thread.is_alive())
      self.assertIsNone(radar.snapshot(time.monotonic(), 'boot'))

  def test_in_memory_targets_expire(self):
    d = PassiveRadar(1)
    d.outputs['front210'] = (10., [dict(trackId=20000001, dRel=15., yRel=3., vRel=1., radarSource='frontRadar')])
    record = dict(hkg={'carState': {'vEgo': 20.}}, age={})
    self.assertTrue(apply_trial(record, 10.1, 'boot', trial=d.snapshot(10.1, 'boot')))
    self.assertEqual(len(record['hkg']['radarTracks']['points']), 1)
    record = dict(hkg={'carState': {'vEgo': 20.}}, age={})
    self.assertFalse(apply_trial(record, 10.3, 'boot', trial=d.snapshot(10.3, 'boot')))
    self.assertNotIn('radarTracks', record['hkg'])
