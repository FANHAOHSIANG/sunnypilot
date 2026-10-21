"""V3 ONNX contract recorded by zoompilot/jetlink d24328d; no hardware claims."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / 'tinygrad_repo'))

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
    from tinygrad import Tensor
    state = Tensor(np.ones(8, np.float32), device='NPY').realize()
    queue = Tensor(np.ones(12, np.float32), device='NPY').realize()
    unrelated = Tensor(np.ones(3, np.float32), device='NPY').realize()
    original = state._buffer()
    self.assertFalse(hasattr(original, 'copyin'))  # The real pinned API, not a stub.
    small = SimpleNamespace(prev_desire=np.ones(8), npy={'desire': np.ones(8)},
                            state_pairs={'state_context': 'next_state_context'},
                            input_queues={'state_context': state, 'features_q': queue, 'action_t': unrelated})
    joined = object.__new__(JoiningModel)
    joined.small = small
    joined._reset_small()
    self.assertIs(state._buffer(), original)
    np.testing.assert_array_equal(state.numpy(), np.zeros(8))
    np.testing.assert_array_equal(queue.numpy(), np.zeros(12))
    np.testing.assert_array_equal(unrelated.numpy(), np.ones(3))
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


class HkgFallbackTests(unittest.TestCase):
  setUp = test_port.ModelTransitionTests.setUp

  def test_actual_pinned_state_buffers_reset_before_same_frame_native_recovery(self):
    from tinygrad import Tensor
    state = Tensor(np.ones(8, np.float32), device='NPY').realize()
    allocation = state._buffer()
    self.local.state_pairs = {'state_context': 'next_state_context'}
    self.local.input_queues = {'state_context': state}
    def native(*args):
      self.assertIs(state._buffer(), allocation)
      np.testing.assert_array_equal(state.numpy(), np.zeros(8))
      return {'local': np.ones(3)}
    self.local.run.side_effect = native
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = TimeoutError('remote timeout')
    self.adapter.warp = Mock(return_value=np.zeros(V3_SPEC.warped_shape, np.uint8))
    self.adapter.active = True
    self.adapter.run({}, {}, self.inputs)
    self.local.run.assert_called_once()
    self.assertFalse(self.fault.exists())
    record = json.loads(self.status.read_text())
    self.assertEqual(record['fallback_count'], 1)
    self.assertEqual(record['recovery_error'], '')

  def test_real_buffer_copy_failure_is_fatal_and_reports_specific_recovery_error(self):
    from tinygrad import Tensor
    from tinygrad.device import Buffer
    state = Tensor(np.ones(8, np.float32), device='NPY').realize()
    self.local.state_pairs = {'state_context': 'next_state_context'}
    self.local.input_queues = {'state_context': state}
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = TimeoutError('remote timeout')
    self.adapter.warp = Mock(return_value=np.zeros(V3_SPEC.warped_shape, np.uint8))
    with patch.object(Buffer, 'copy_from', side_effect=RuntimeError('state reset transfer failed')):
      with self.assertRaisesRegex(RuntimeError, 'state reset transfer failed'):
        self.adapter.run({}, {}, self.inputs)
    self.local.run.assert_not_called()
    self.assertTrue(self.fault.exists())
    record = json.loads(self.status.read_text())
    self.assertEqual(record['phase'], 'local_failed')
    self.assertIn('state reset transfer failed', record['recovery_error'])
    self.assertEqual(record['fallback_count'], 0)


if __name__ == '__main__':
  unittest.main()
