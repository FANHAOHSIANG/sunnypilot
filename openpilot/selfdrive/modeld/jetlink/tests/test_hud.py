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
      'radarState': dict(leadOne=dict(present=True, dRel=30., yRel=1.5, vRel=-2.)),
      'deviceState': dict(started=True),
      'radarTracks': dict(points=[]),
    }

  def __getitem__(self, name):
    return self.values[name]


class SnapshotTests(unittest.TestCase):
  def test_extra_tracks_require_real_lateral_position_and_no_radar_error(self):
    sm = Sources()
    points = [dict(trackId=1, dRel=20., yRel=3.5, vRel=-2.),
              dict(trackId=2, dRel=25., yRel=-3.5, vRel=1.),
              dict(trackId=3, dRel=15., yRel=float('nan'), vRel=0.),
              dict(trackId=4, dRel=10., vRel=0.),
              dict(trackId=5, dRel=0., yRel=3., vRel=0.)]
    sm.values['radarTracks'] = dict(points=points)
    data = snapshot(sm, 10.05, True, True)['hkg']['radarTracks']
    self.assertEqual([p['trackId'] for p in data['points']], [1, 2])
    for flag in ('canError', 'radarFault', 'wrongConfig', 'radarUnavailableTemporary'):
      sm.values['radarTracks']['errors'] = {flag: True}
      self.assertNotIn('radarTracks', snapshot(sm, 10.05, True, True)['hkg'])

  def test_extra_tracks_are_bounded_and_cannot_displace_geometry(self):
    sm = Sources()
    sm.values['radarTracks'] = dict(points=[dict(trackId=i, dRel=float(i + 1), yRel=3.5, vRel=1.) for i in range(100)])
    record = snapshot(sm, 10.05, True, True)
    self.assertEqual(len(record['hkg']['radarTracks']['points']), 32)
    self.assertLessEqual(len(encode(record)), hud.MAX_BYTES)
    base = dict(record, hkg={k: v for k, v in record['hkg'].items() if k != 'radarTracks'})
    # Base geometry fits; only the optional extra tracks cause overflow.
    record['padding'] = 'x' * (hud.MAX_BYTES - len(encode(base)) - 40)
    decoded = json.loads(encode(record))
    self.assertIn('modelV2', decoded['hkg'])
    self.assertNotIn('radarTracks', decoded['hkg'])

  def test_current_present_and_legacy_status_are_normalized(self):
    for flags, expected in (({'present': True}, True), ({'present': False}, False),
                            ({'present': False, 'status': True}, False),
                            ({'status': True}, True), ({}, False)):
      with self.subTest(flags=flags):
        sm = Sources()
        sm.values['radarState'] = {key: dict(flags, dRel=30.) for key in ('leadOne', 'leadTwo')}
        radar = json.loads(encode(snapshot(sm, 10.05, True, True)))['hkg']['radarState']
        self.assertEqual([radar[k]['status'] for k in ('leadOne', 'leadTwo')], [expected] * 2)

  def test_visual_leads_keep_current_sample_and_budget(self):
    sm = Sources()
    lead = dict(prob=.9, probTime=0., **{k: list(range(6)) for k in ('x', 'y', 'v', 'a', 'xStd', 'yStd')})
    sm.values['modelV2'].update(leadsV3=[lead] * 3, velocity={'x': list(range(33))})
    payload = encode(snapshot(sm, 10.05, True, True))
    model = json.loads(payload)['hkg']['modelV2']
    self.assertEqual(len(model['leadsV3']), 3)
    self.assertEqual(model['leadsV3'][0]['x'], [0])
    self.assertEqual(model['velocity']['x'], [0])
    self.assertLessEqual(len(payload), hud.MAX_BYTES)

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

  def test_only_exact_single_enqueued_request_allows_early_hud(self):
    self.client.pending = [SimpleNamespace(seq=5)]
    self.assertFalse(self.sender.send(self.client, after_seq=4))
    self.assertFalse(self.sender.send(self.client))
    self.client.pending.append(SimpleNamespace(seq=6))
    self.assertFalse(self.sender.send(self.client, after_seq=6))
    self.client.pending.pop()
    self.assertFalse(self.sender.send(self.client, deadline=10.052, after_seq=5))
    self.client.t.try_send.assert_not_called()
    self.assertTrue(self.sender.send(self.client, deadline=10.1, after_seq=5))
    self.client.t.recv.assert_not_called()

  def test_early_hud_keeps_backpressure_and_staleness_guards(self):
    self.client.pending = [SimpleNamespace(seq=5)]
    self.client.t.try_send.return_value = False
    self.assertFalse(self.sender.send(self.client, after_seq=5))
    self.client.t.send.assert_not_called()
    self.assertEqual(self.sender.last_stamp, 0.)
    self.clock.return_value = 10.5
    self.client.t.try_send.reset_mock()
    self.assertFalse(self.sender.send(self.client, after_seq=5))
    self.client.t.try_send.assert_not_called()

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
