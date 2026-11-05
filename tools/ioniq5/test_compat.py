from pathlib import Path
from types import SimpleNamespace as NS
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DONOR = HERE / 'carrot-reference'
sys.path[:0] = [str(ROOT), str(HERE), str(DONOR / 'third_party/jetlink')]
from openpilot.hud.ioniq5.live import decode_tpms
from openpilot.selfdrive.modeld.n16_shadow import N16Shadow
from parked import parked, model_status
from shadow import CurveShadow


def test_display_inputs():
  raw = bytearray(16)
  raw[4:8] = bytes([48, 49, 0, 255])
  raw[8] = 8
  assert decode_tpms(raw) == {'fl': 34.8, 'fr': 35.525}
  assert not decode_tpms(raw[:8])
  assert parked(NS(gearShifter='park', standstill=True, vEgo=0))
  assert not parked(NS(gearShifter='drive', standstill=True, vEgo=0))


def test_missing_or_stale_vehicle_state_has_no_shadow_result(tmp_path):
  c = CurveShadow()
  assert c.feed('modelV2', NS(), 12) is None
  for name in ('carState', 'carControl','controlsState'):
    c.feed(name, NS(), 12)
  assert c.feed('modelV2', NS(), 13) is None
  assert c.feed('modelV2', None, 14, False) is None
  assert not any(model_status(tmp_path, 10).values())


@pytest.fixture
def client():
  import json
  from jetlink.spec import ModelSpec
  spec = ModelSpec.from_dict(json.loads((DONOR / 'openpilot/selfdrive/modeld/jetlink/jetson_model.json').read_text()))
  return NS(spec=spec, project_output=True, output_projection_available=True)


def test_exact_prefix_matches_full_using_hcop_parser(client):
  from jetlink import output_projection as projection
  adapter = N16Shadow(client)
  full = np.random.default_rng(12).normal(0, .1, client.spec.output_nelem).astype('float32')
  saved = full.copy()
  complete = adapter.parse(full)
  projected = adapter.parse(full[:projection.COUNT])
  assert complete['plan'].shape == (1, 33, 15) and complete['action'].shape == (1, 4)
  assert complete.keys() == projected.keys()
  for k in complete:
    np.testing.assert_array_equal(complete[k], projected[k])
  np.testing.assert_array_equal(full, saved)
  client.output_projection_available = False
  with pytest.raises(ValueError):
    adapter.parse(full[:projection.COUNT])
  full[0] = np.nan
  with pytest.raises(ValueError):
    adapter.parse(full)


def test_mac_hud_reader_decodes_hcop_speed_tpms_and_tracks(tmp_path, monkeypatch):
  import base64
  from hud_bridge import packet, schema_at
  sys.path.insert(0, str(DONOR / 'tools/jetlink'))
  import hud_protocol
  schema = schema_at(DONOR)
  source = dict(ts=100., alive={'carState': True},
                carState=dict(vEgo=12.5, cruiseSpeed=20., cruiseEnabled=True, cruiseAvailable=True,
                              tpms={'fl': 38., 'fr': 37., 'rl': 36., 'rr': 35.}),
                ioniq5Display=dict(carStateMono=99_990_000_000, carStateValid=True, radarMono=99_975_000_000,
                                   points=[dict(trackId=10, dRel=24., yRel=-3.5, vRel=2., radarSource='frontRadar')]))
  record = packet(schema, source)
  clock = hud_protocol.time.monotonic()
  path = tmp_path / 'bench.packet'
  path.write_bytes(hud_protocol.HEADER.pack(clock) + hud_protocol.encode_snapshot(record))
  monkeypatch.setattr(hud_protocol, 'HUD_PACKET', path)
  monkeypatch.setattr(hud_protocol, '_next_snapshot_read', 0.)
  _, received = hud_protocol.read_snapshot()
  with schema.Event.from_bytes(base64.b64decode(received['events']['carState'])) as msg:
    assert msg.which() == 'carState' and msg.carState.tpms.rr == 35 and msg.carState.vCruise == 72.
  with schema.Event.from_bytes(base64.b64decode(received['events']['liveTracks'])) as msg:
    assert msg.liveTracks.points[0].yRel == -3.5
  assert base64.b64decode(received['params']['ClusterHudScreenMode']) == b'7'
  assert packet(schema, dict(source, ts=101.)) is None
  assert packet(schema, dict(source, stale=True)) is None


def test_radar_requires_complete_fresh_repeated_cycles_on_selected_bus():
  from openpilot.hud.ioniq5.radar import PassiveRadar
  decoder = PassiveRadar(1)
  raw = ((100 << 40) | (600 << 64) | (60 << 76)).to_bytes(32, 'little')

  def cycle(t, bus=1, missing=None):
    for address in range(0x210, 0x220):
      if address != missing:
        decoder.feed(address, bus, raw, t, t)

  cycle(.1, bus=0)
  assert not decoder.snapshot(.1, '')['points']
  cycle(.2)
  cycle(.25)
  assert not decoder.snapshot(.25, '')['points']
  cycle(.3)
  snapshot = decoder.snapshot(.3, '')
  assert len(snapshot['points']) == 16
  assert snapshot['points'][0]['dRel'] == 30.
  assert not decoder.snapshot(.7, '')['points']
  cycle(.8, missing=0x210)
  assert not decoder.snapshot(.8, '')['points']


def test_curve_state_is_cleared_when_model_gaps_or_driver_overrides():
  c = CurveShadow()
  def frame(t, gas=False):
    states = {'carState': NS(vEgo=20., gasPressed=gas, brakePressed=False),
              'controlsState': NS(curvature=.01), 'carControl': NS(longActive=True)}
    for k, v in states.items():
      c.feed(k, v, t)
    model = NS(position=NS(x=np.linspace(0., 150., 33)), velocity=NS(x=np.full(33, 20.)),
               orientationRate=NS(z=np.full(33, .3)), meta=NS(laneChangeState='off'))
    return c.feed('modelV2', model, t)
  assert frame(1.)['evaluated']
  curve = c.curve
  assert frame(1.05, gas=True) is None and c.curve.req_f == 0.
  assert frame(2.)['evaluated'] and c.curve is not curve
