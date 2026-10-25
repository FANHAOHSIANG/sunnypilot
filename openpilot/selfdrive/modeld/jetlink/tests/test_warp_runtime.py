import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.selfdrive.modeld.jetlink.warp_runtime import COHERENT_WRITEBACK, make_coherent, prepare_small_reset


class WarpRuntimeTests(unittest.TestCase):
  def test_noncoherent_allocation_is_freed_without_replacing_original_output(self):
    out, dev = Mock(nbytes=393216), Mock()
    mem = SimpleNamespace(meta=(SimpleNamespace(flags=COHERENT_WRITEBACK & ~(1 << 31)),))
    dev._gpu_alloc.return_value = mem
    with self.assertRaisesRegex(RuntimeError, 'coherent'):
      make_coherent(out, dev)
    dev.synchronize.assert_called_once()
    dev._gpu_free.assert_called_once_with(mem)
    out.deallocate.assert_not_called()

  def test_coherent_mapping_retains_requested_size_and_flags(self):
    out, dev = Mock(nbytes=393216), Mock()
    mem = SimpleNamespace(meta=(SimpleNamespace(flags=COHERENT_WRITEBACK | 8),))
    dev._gpu_alloc.return_value = mem
    make_coherent(out, dev)
    dev._gpu_alloc.assert_called_once_with(393216, flags=COHERENT_WRITEBACK)
    out.allocate.assert_called_once_with(opaque=mem)

  def test_captured_reset_clears_history_and_preserves_buffers(self):
    if not shutil.which('clang'):
      self.skipTest('CPU captured-reset test requires clang')
    from tinygrad import Tensor
    history = Tensor(np.ones(8, np.float32), device='CPU').realize()
    other = Tensor(np.ones(8, np.float32), device='CPU').realize()
    small = SimpleNamespace(input_queues={'state_context': history, 'action_t': other},
                            state_pairs={'state_context': 'next_state_context'},
                            npy={'desire': np.ones(8, np.float32)}, prev_desire=np.ones(8, np.float32))
    buffer = history._buffer()
    reset = prepare_small_reset(small)
    history.assign(3).realize()
    reset()
    self.assertIs(history._buffer(), buffer)
    np.testing.assert_array_equal(history.numpy(), np.zeros(8))
    np.testing.assert_array_equal(other.numpy(), np.ones(8))
    np.testing.assert_array_equal(small.npy['desire'], np.zeros(8))
    np.testing.assert_array_equal(small.prev_desire, np.zeros(8))


if __name__ == '__main__':
  unittest.main()
