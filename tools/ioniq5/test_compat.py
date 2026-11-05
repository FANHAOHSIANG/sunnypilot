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
