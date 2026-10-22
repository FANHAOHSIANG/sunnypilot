import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.selfdrive.modeld.jetlink import hud
from openpilot.selfdrive.modeld.jetlink.hud_publisher import SERVICES, snapshot, encode, select, LINE


class Sources:
  def __init__(self):
    self.valid = self.alive = dict.fromkeys(SERVICES, True)
    self.recv_time = dict.fromkeys(SERVICES, 10.)
    line = {k: [i * .1 for i in range(33)] for k in ('x', 'y', 'z', 't')}
    self.values = {
      'carState': dict(vEgo=20., vEgoCluster=20.5, vCruiseCluster=0., leftBlinker=True,
                       cruiseState=dict(enabled=True, speed=25.)),
      'controlsState': dict(deprecated=dict(vCruise=90.)),
      'selfdriveState': dict(enabled=False, alertSize='none'),
      'carControl': dict(latActive=True, longActive=False),
      'modelV2': dict(position=line, laneLines=[line] * 4, laneLineProbs=[.1, .9, .9, .1], roadEdges=[line] * 2),
      'radarState': dict(leadOne=dict(status=True, dRel=30., yRel=1.5, vRel=-2.)),
      'deviceState': dict(started=True),
    }

  def __getitem__(self, name):
    return self.values[name]


class SnapshotTests(unittest.TestCase):
  def test_units_axes_mads_and_payload_budget(self):
    record = snapshot(Sources(), 10.05, True, True, True)
    data = record['hkg']
    self.assertEqual(data['carState']['vEgo'], 20.)
    self.assertEqual(data['carState']['vCruiseCluster'], 90.)
    self.assertEqual(data['radarState']['leadOne']['yRel'], 1.5)
    self.assertTrue(data['carControl']['latActive'])
    self.assertFalse(data['selfdriveState']['enabled'])
    self.assertLessEqual(len(encode(record)), hud.MAX_BYTES)
    self.assertEqual(len(data['modelV2']['position']['x']), 17)
    self.assertEqual(data['modelV2']['position']['x'][-1], 3.2)
    self.assertEqual(record['events'], {})

  def test_expired_invalid_and_future_sources_removed(self):
    sm = Sources()
    sm.recv_time['carState'] = 9.
    sm.recv_time['modelV2'] = 11.
    sm.valid['radarState'] = False
    data = snapshot(sm, 10.1, True, True)['hkg']
    self.assertNotIn('carState', data)
    self.assertNotIn('modelV2', data)
    self.assertNotIn('radarState', data)
    self.assertIn('deviceState', data)

  def test_nonfinite_never_encoded_and_zero_speed_remains_zero(self):
    sm = Sources()
    sm.values['carState'].update(vEgo=0., aEgo=float('nan'))
    record = snapshot(sm, 10.1, True, True)
    parsed = json.loads(encode(record))
    self.assertEqual(parsed['hkg']['carState']['vEgo'], 0.)
    self.assertNotIn('aEgo', parsed['hkg']['carState'])
    self.assertEqual(select({'x': [float('inf')]}, LINE)['x'], [])

  def test_packet_bounds_staleness_and_reboot_clock(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'packet'
      for stamp, payload, good in ((10., b'{}', True), (9., b'{}', False), (11., b'{}', False),
                                    (float('nan'), b'{}', False), (10., b'x' * (hud.MAX_BYTES + 1), False)):
        path.write_bytes(hud.HEADER.pack(stamp) + payload)
        self.assertEqual(hud.read_packet(path, now=10.1) is not None, good)
      path.write_bytes(b'bad')
      self.assertIsNone(hud.read_packet(path, now=10.1))


class SenderTests(unittest.TestCase):
  def setUp(self):
    self.sender = hud.HudSender({})  # no background reader in deterministic tests
    self.sender.latest = (10., b'{}')
    self.client = SimpleNamespace(t=Mock(), pending=[], _next_seq=Mock(return_value=9))
    self.client.t.try_send.return_value = True
    self.clock = patch.object(hud.time, 'monotonic', return_value=10.05)
    self.clock.start()
    self.addCleanup(self.clock.stop)
    self.addCleanup(self.sender.close)

  def test_one_way_uses_owner_sequence_and_never_receives(self):
    self.assertTrue(self.sender.send(self.client, deadline=10.1))
    self.client.t.try_send.assert_called_once_with(0x4000, 9, (b'{}',))
    self.client.t.recv.assert_not_called()
    self.assertFalse(self.sender.send(self.client, deadline=10.1))

  def test_pending_inference_and_small_budget_skip(self):
    self.client.pending = [1]
    self.assertFalse(self.sender.send(self.client))
    self.client.pending = []
    self.assertFalse(self.sender.send(self.client, deadline=10.052))
    self.client._next_seq.assert_not_called()

  def test_usb_backpressure_never_calls_blocking_send(self):
    self.client.t.try_send.return_value = False
    self.assertFalse(self.sender.send(self.client))
    self.client.t.send.assert_not_called()
    self.assertEqual(self.sender.last_stamp, 0.)

  def test_transport_failure_disables_only_hud(self):
    self.client.t.try_send.side_effect = RuntimeError('USB busy')
    with self.assertLogs('sunnypilot.jetlink.hud', level='ERROR'):
      self.assertFalse(self.sender.send(self.client))
    self.assertTrue(self.sender.disabled)
    self.client.t.close.assert_not_called()

  def test_closed_or_old_sender_cannot_write_after_handoff(self):
    self.sender.latest = (8., b'{}')
    self.assertFalse(self.sender.send(self.client))
    self.sender.latest = (10., b'{}')
    self.sender.close()
    self.assertFalse(self.sender.send(self.client))
    self.client.t.try_send.assert_not_called()

  def test_capability_and_explicit_opt_in_required(self):
    with patch.object(hud, 'ENABLE') as enabled:
      enabled.is_file.return_value = True
      self.assertFalse(hud.supported({}))
      self.assertFalse(hud.supported({hud.CAPABILITY: 1}))
      self.assertTrue(hud.supported({hud.CAPABILITY: True}))
      enabled.is_file.return_value = False
      self.assertFalse(hud.supported({hud.CAPABILITY: True}))


if __name__ == '__main__':
  unittest.main()
