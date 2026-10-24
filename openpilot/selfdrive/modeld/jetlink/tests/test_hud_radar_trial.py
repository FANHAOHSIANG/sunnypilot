import copy
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import (
  PassiveRadar, FAMILIES, SOURCE, apply_trial, valid_point,
)
from openpilot.selfdrive.modeld.jetlink.hud_publisher import snapshot, encode, MAX_BYTES
from openpilot.selfdrive.modeld.jetlink.tests.test_hud import Sources

VECTORS = json.loads((Path(__file__).parent / 'fixtures/hud_radar_vectors.json').read_text())['vectors']


def cycle(decoder, vector, stamp, bus=1, skip=None, raw_override=None, slot=None):
  name = vector['family']
  start, count, length = FAMILIES[name]
  target = vector['address'] if slot is None else slot
  for address in range(start, start + count):
    if address == skip:
      continue
    raw = (raw_override if raw_override is not None else bytes.fromhex(vector['raw'])) if address == target else bytes(length)
    decoder.feed(address, bus, raw, stamp, stamp)


class DecoderTests(unittest.TestCase):
  def test_all_five_families_match_independent_dbc_encoded_vectors(self):
    for vector in VECTORS:
      with self.subTest(family=vector['family']):
        decoder = PassiveRadar(1)
        for index in range(3):
          cycle(decoder, vector, 10 + .05 * index)
          points = decoder.snapshot(10 + .05 * index, 'boot')['points']
          if index < 2:
            self.assertEqual(points, [])
        actual = sorted((p['dRel'], p['yRel'], p['vRel']) for p in points)
        expected = sorted(vector['expected'])
        self.assertEqual(len(actual), len(expected))
        for row, want in zip(actual, expected):
          for value, other in zip(row, want):
            self.assertAlmostEqual(value, other)
        self.assertEqual(decoder.snapshot(10.351, 'boot')['points'], [])

  def test_wrong_bus_echo_wrong_dlc_and_old_can_never_decode(self):
    vector = VECTORS[0]
    decoder = PassiveRadar(1)
    for i in range(5):
      cycle(decoder, vector, 10 + i * .05, bus=0)
      cycle(decoder, vector, 10 + i * .05, bus=129)
    for address in range(0x210, 0x220):
      decoder.feed(address, 1, bytes(24), 10.2, 10.2)
      decoder.feed(address, 1, bytes.fromhex(vector['raw']), 9., 10.2)
      decoder.feed(address, 1, bytes.fromhex(vector['raw']), 11., 10.2)
    self.assertEqual(decoder.snapshot(10.2, 'boot')['points'], [])
    self.assertFalse(decoder.cycles)

  def test_incomplete_cycle_clears_and_recovery_requires_warmup(self):
    vector = VECTORS[0]
    decoder = PassiveRadar(1)
    for i in range(3):
      cycle(decoder, vector, 10 + i * .05)
    self.assertTrue(decoder.snapshot(10.1, 'boot')['points'])
    cycle(decoder, vector, 10.15, skip=0x211)
    self.assertEqual(decoder.snapshot(10.15, 'boot')['points'], [])
    cycle(decoder, vector, 10.2)
    self.assertEqual(decoder.snapshot(10.2, 'boot')['points'], [])

  def test_invalid_objects_replace_old_data_immediately(self):
    for vector in VECTORS:
      decoder = PassiveRadar(1)
      for i in range(3):
        cycle(decoder, vector, 10 + i * .05)
      cycle(decoder, vector, 10.15, raw_override=bytes(FAMILIES[vector['family']][2]))
      self.assertEqual(decoder.snapshot(10.15, 'boot')['points'], [])

  def test_conflicting_front_families_are_not_guessed(self):
    decoder = PassiveRadar(1)
    for i in range(3):
      for vector in VECTORS[:2]:
        cycle(decoder, vector, 10 + i * .05)
    result = decoder.snapshot(10.1, 'boot')
    self.assertTrue(result['ambiguous_front'])
    self.assertEqual(result['points'], [])

  def test_group3_native_identity_survives_slot_migration(self):
    decoder = PassiveRadar(1)
    for i in range(3):
      cycle(decoder, VECTORS[2], 10 + i * .05)
    before = decoder.snapshot(10.1, 'boot')['points'][0]['trackId']
    cycle(decoder, VECTORS[2], 10.15, slot=0x401)
    self.assertEqual(decoder.snapshot(10.15, 'boot')['points'][0]['trackId'], before)

  def test_near_side_requires_real_corner_position(self):
    point = dict(trackId=1, dRel=0., yRel=-3., vRel=1., radarSource='corner180')
    self.assertTrue(valid_point(point))
    self.assertFalse(valid_point(dict(point, radarSource='frontRadar')))
    self.assertFalse(valid_point(dict(point, yRel=0.)))
    self.assertFalse(valid_point(dict(point, yRel=float('nan'))))


class DeliveryTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.path = Path(self.temp.name) / 'trial.json'
    self.decoder = PassiveRadar(1)
    for i in range(3):
      cycle(self.decoder, VECTORS[0], 10 + i * .05)
    self.record = self.decoder.snapshot(10.1, 'boot')

  def write(self, value):
    self.path.write_text(json.dumps(value))

  def test_trial_only_changes_hud_track_payload_preserves_control_and_primary(self):
    record = snapshot(Sources(), 10.11, True, True)
    before = copy.deepcopy(record)
    self.write(self.record)
    self.assertTrue(apply_trial(record, 10.11, 'boot', self.path))
    for name in ('radarState', 'carState', 'carControl', 'modelV2'):
      self.assertEqual(record['hkg'][name], before['hkg'][name])
    self.assertEqual(len(record['hkg']['radarTracks']['points']), 2)
    self.assertLessEqual(len(encode(record)), MAX_BYTES)
    self.assertEqual(record['events'], {})

  def test_stale_other_boot_invalid_file_and_missing_ego_do_not_override_native(self):
    mutations = [dict(self.record, boot_id='other'), dict(self.record, updated=9.),
                 dict(self.record, updated=11.), dict(self.record, updated=float('nan')),
                 dict(self.record, points=[dict(self.record['points'][0], age=1.)]),
                 dict(self.record, points=[dict(self.record['points'][0], age=float('nan'))]),
                 dict(self.record, points=[dict(self.record['points'][0], yRel=float('nan'))]),
                 dict(self.record, source='unknown'), [], {}]
    for value in mutations:
      record = snapshot(Sources(), 10.11, True, True)
      before = copy.deepcopy(record)
      self.write(value)
      self.assertFalse(apply_trial(record, 10.11, 'boot', self.path))
      self.assertEqual(record, before)
    self.path.write_bytes(b'x' * 16385)
    self.assertFalse(apply_trial(record, 10.11, 'boot', self.path))
    self.write(self.record)
    record['hkg'].pop('carState')
    self.assertFalse(apply_trial(record, 10.11, 'boot', self.path))

  def test_native_duplicate_preferred_and_file_removal_restores_native(self):
    sm = Sources()
    sm.values['radarTracks'] = dict(points=[dict(trackId=101, dRel=20., yRel=3.5, vRel=-2.)])
    record = snapshot(sm, 10.11, True, True)
    self.write(self.record)
    self.assertTrue(apply_trial(record, 10.11, 'boot', self.path))
    points = record['hkg']['radarTracks']['points']
    self.assertEqual(len(points), 2)
    self.assertEqual(points[0]['trackId'], 101)
    self.path.unlink()
    restored = snapshot(sm, 10.11, True, True)
    self.assertFalse(apply_trial(restored, 10.11, 'boot', self.path))
    self.assertEqual(len(restored['hkg']['radarTracks']['points']), 1)


if __name__ == '__main__':
  unittest.main()
