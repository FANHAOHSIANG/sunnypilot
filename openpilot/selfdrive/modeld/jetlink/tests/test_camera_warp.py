"""CPU pixel/layout check of sunnypilot's native warp against NumPy NV12 sampling.

Requires the pinned tinygrad submodule. This establishes CPU pixel/layout
sampling parity away from float32 half-pixel boundaries, not QCOM GPU execution, serialization or realtime timing.
"""
import ast
from collections import namedtuple
from pathlib import Path
import sys
import shutil
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / 'tinygrad_repo'))


class CameraWarpTests(unittest.TestCase):
  def test_native_warp_matches_nv12_reference_with_padding_and_both_cameras(self):
    try:
      from tinygrad import Tensor, Device
      from tinygrad.helpers import Context
    except ImportError:
      self.skipTest('Initialize the pinned tinygrad submodule to run CPU pixel checks')
    if not shutil.which('clang'):
      self.skipTest('CPU pixel checks require clang; QCOM execution still requires C4 hardware')
    if Device.DEFAULT != 'CPU':
      self.skipTest('Run with DEV=CPU for the hardware-independent pixel check')

    # Load the actual native warp function bodies without the compiler's
    # unrelated hardware detection/firmware side effects on a workstation.
    source = ast.parse((ROOT / 'tinygrad_repo/examples/openpilot/compile_warp.py').read_text())
    names = {'warp_perspective_tinygrad', 'frames_to_tensor', 'make_frame_prepare', 'make_warp'}
    scope = {'Tensor': Tensor, 'Device': Device, 'Context': Context, 'NV12Frame': tuple}
    code = ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
    exec(compile(code, '<native sunnypilot camera warp>', 'exec'), scope)
    nv12 = namedtuple('NV12', 'width height stride y_height uv_height size')(64, 48, 128, 64, 32, 12288)
    prepare = scope['make_frame_prepare'](nv12, 512, 256)
    def warp(tfm, big_tfm, frame, big_frame):
      return prepare(frame, tfm).stack(prepare(big_frame, big_tfm))
    rng = np.random.default_rng(7527)
    images = [rng.integers(0, 256, nv12.size, dtype=np.uint8) for _ in range(2)]

    def reference(frame, matrix, direction=(0, 0)):
      def plane(chroma=False, channel=0):
        width, height = (256, 128) if chroma else (512, 256)
        x, y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        m = matrix * (np.array([[1, 1, .5], [1, 1, .5], [2, 2, 1]], np.float32) if chroma else 1)
        denominator = m[2, 0] * x + m[2, 1] * y + m[2, 2]
        sx = (m[0, 0] * x + m[0, 1] * y + m[0, 2]) / denominator
        sy = (m[1, 0] * x + m[1, 1] * y + m[1, 2]) / denominator
        # Fused CPU float32 arithmetic can land on either side of an exact
        # half-pixel. Permit only source pixels reached within 16 float32
        # ulps of the projected coordinate; every other pixel stays exact.
        epsilon = 16 * np.finfo(np.float32).eps
        sx = np.rint(sx + direction[0] * epsilon * np.maximum(1, np.abs(sx)))
        sy = np.rint(sy + direction[1] * epsilon * np.maximum(1, np.abs(sy)))
        sx = sx.clip(0, nv12.width // (2 if chroma else 1) - 1).astype(int)
        sy = sy.clip(0, nv12.height // (2 if chroma else 1) - 1).astype(int)
        at = (nv12.stride * nv12.y_height + sy * nv12.stride + sx * 2 + channel
              if chroma else sy * nv12.stride + sx)
        return frame[at]
      y = plane()
      return np.stack([y[::2, ::2], y[1::2, ::2], y[::2, 1::2], y[1::2, 1::2], plane(True, 0), plane(True, 1)])

    matrices = [np.eye(3, dtype=np.float32), np.array([[.125, .01, .3], [-.02, .18, .1], [.0001, -.0002, 1]], np.float32),
                np.array([[1, 0, -100], [0, 1, 1000], [0, 0, 1]], np.float32)]
    for i, m in enumerate(matrices):
      wide = m * np.array([[1.01, 1, 1], [1, .99, 1], [1, 1, 1]], np.float32)
      with self.subTest(matrix=i):
        actual = warp(Tensor(m, device='NPY'), Tensor(wide, device='NPY'),
                      Tensor(images[0], device='CPU'), Tensor(images[1], device='CPU')).numpy()
        expected = np.stack([reference(images[0], m), reference(images[1], wide)])
        self.assertEqual(actual.shape, (2, 6, 128, 256))
        self.assertEqual(actual.dtype, np.uint8)
        allowed = actual == expected
        for direction in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
          candidate = np.stack([reference(images[0], m, direction), reference(images[1], wide, direction)])
          allowed |= actual == candidate
        self.assertTrue(allowed.all(), f"{np.count_nonzero(~allowed)} pixels differ outside float32 half-pixel boundaries")


if __name__ == '__main__':
  unittest.main()
