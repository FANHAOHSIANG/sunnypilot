"""V3 ONNX contract recorded by zoompilot/jetlink d24328d; no hardware claims."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import numpy as np

import test_port
from jetlink.spec import ModelSpec
from jetlink.registry.catalog import DEFAULT_BIG_MODEL_REF, merge_catalogs, parse_catalog
from openpilot.selfdrive.modeld.jetlink.contracts import V3_SPEC, validate_model_spec, parse_outputs, model_name
from openpilot.selfdrive.modeld.jetlink.model import JoiningModel
from openpilot.selfdrive.modeld.parse_model_outputs import Parser


class V3Tests(unittest.TestCase):
  def test_v3_contract_and_stock_parser(self):
    validate_model_spec(V3_SPEC)
    self.assertTrue(V3_SPEC.stateful)
    self.assertEqual(V3_SPEC.packed_nbytes, 48)  # scalars only; history stays on the Mac
    self.assertEqual(model_name(V3_SPEC), 'Cinque Terre V3')
    outputs = parse_outputs(Parser(), V3_SPEC, np.zeros(V3_SPEC.output_nelem, np.float32))
    self.assertEqual(outputs['plan'].shape, (1, 33, 15))
    self.assertEqual(outputs['action'].shape, (1, 4))
    self.assertTrue(np.isfinite(outputs['plan']).all())

  def test_v3_checkpoint_is_descriptive_but_shapes_and_size_are_pinned(self):
    raw = deepcopy(V3_SPEC.to_dict())
    raw['checkpoint'] = 'f78ed37d/12864'
    validate_model_spec(ModelSpec.from_dict(raw))
    raw['nbytes'] += 1
    with self.assertRaises(ValueError):
      validate_model_spec(ModelSpec.from_dict(raw))

  def test_new_native_state_pairs_are_cleared_on_remote_failure(self):
    class Buffer:
      def copyin(self, data):
        self.value = bytes(data)
    class Tensor:
      dtype = SimpleNamespace(itemsize=4)
      def __init__(self):
        self.buffer = Buffer()
      def numel(self):
        return 8
      def _buffer(self):
        return self.buffer
    state = Tensor()
    small = SimpleNamespace(prev_desire=np.ones(8), npy={'desire': np.ones(8)},
                            state_pairs={'state_context': 'next_state_context'}, input_queues={'state_context': state})
    joined = object.__new__(JoiningModel)
    joined.small = small
    joined._reset_small()
    self.assertEqual(state.buffer.value, bytes(32))
    self.assertEqual(small.npy['desire'].sum(), 0)

  def test_v3_smoothing_applies_only_when_active(self):
    joined = object.__new__(JoiningModel)
    joined.spec = V3_SPEC
    joined.active = False
    self.assertEqual(joined.lat_smooth_seconds, 0)
    joined.active = True
    self.assertEqual(joined.lat_smooth_seconds, .1)

  def test_new_catalog_keeps_v3_remote_only_when_selector_differs(self):
    bundle = dict(ref=DEFAULT_BIG_MODEL_REF, is_big=True, display_name='Cinque Terre V3',
                  minimum_selector_version='21', generation='12', models=[{'artifact': 'GPU pkl'}])
    merged = merge_catalogs([{'bundles': [bundle]}])
    self.assertEqual(parse_catalog(merged)[0].ref, DEFAULT_BIG_MODEL_REF)
    self.assertEqual(merged['bundles'][0]['models'], [])


if __name__ == '__main__':
  unittest.main()
