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


class TestTpms(unittest.TestCase):
  def test_recorded_pressure_and_invalid_sentinels(self):
    from openpilot.selfdrive.modeld.jetlink.hud_radar_live import decode_tpms
    raw = bytes.fromhex('6301b700272828290000000000000000')
    self.assertEqual(decode_tpms(raw), dict(fl=39., fr=40., rl=40., rr=41.))
    self.assertEqual(decode_tpms(bytes.fromhex('6301b700ffffffff0000000000000000')), {})
    self.assertEqual(decode_tpms(b''), {})
    converted = bytearray(raw)
    converted[8] = 8
    converted[4:8] = bytes([55]*4)
    self.assertEqual(decode_tpms(converted)['fl'], 39.875)

  def test_tpms_expires(self):
    radar = LiveRadar.__new__(LiveRadar)
    radar.lock = threading.Lock()
    radar.tpms = (10., {'fl': 39.})
    self.assertEqual(radar.tpms_snapshot(12.), {'fl': 39.})
    self.assertIsNone(radar.tpms_snapshot(15.))
