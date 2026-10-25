"""Exercise HKG's actual pinned tinygrad serializer, without a GPU."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

from openpilot.selfdrive.modeld.jetlink.artifact import load_prepared, save_prepared


class HkgArtifactTests(unittest.TestCase):
  def test_preparation_cross_process_bytes_use_hkg_stream_abi(self):
    root = Path(__file__).resolve().parents[5] / 'tinygrad_repo'
    if not (root / 'tinygrad').is_dir():
      self.skipTest('Initialize HKG pinned tinygrad to check its serialization ABI')
    sys.path.insert(0, str(root))
    value = {'identity': (1344, 760, 2048000, 'a' * 64), 'pixels': np.arange(4096, dtype=np.uint8)}
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'warp.pkl'
      save_prepared(value, path)
      restored = load_prepared(path.read_bytes())
    self.assertEqual(restored['identity'], value['identity'])
    np.testing.assert_array_equal(restored['pixels'], value['pixels'])
