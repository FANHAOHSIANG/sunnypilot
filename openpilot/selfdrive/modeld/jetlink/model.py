"""Native camera warp and a guarded switch between local and Jetlink inference."""
import json
import os
from pathlib import Path
import time

import numpy as np

from openpilot.selfdrive.modeld.jetlink.link import ClientConnection, SPEC, FAULT, state
from openpilot.selfdrive.modeld.jetlink.prepare import WarpPreparation
from openpilot.selfdrive.modeld.jetlink.contracts import model_name, parse_outputs, V3_SPEC
from openpilot.selfdrive.modeld.jetlink.gate import may_join
from openpilot.selfdrive.modeld.jetlink.artifact import load_prepared, save_prepared, stream_abi

from openpilot.common.jetlink_status import MODEL_STATUS


def make_warp_inputs():
  from tinygrad import Tensor
  transforms = {k: np.eye(3, dtype=np.float32) for k in ('tfm', 'big_tfm')}
  return transforms, {k: Tensor(v, device='NPY').realize() for k, v in transforms.items()}


class Warp:
  def __init__(self, width, height, prepared=None, input_buffers=None):
    started, cpu_started = time.monotonic(), time.thread_time()
    from tinygrad import Device
    from openpilot.system.camerad.cameras.nv12_info import get_nv12_info
    # The local model already uses QCOM explicitly. DEFAULT can still be unset
    # and probe every earlier backend, blocking live inference for hundreds of ms.
    if Device['QCOM'].device != 'QCOM' or Device.DEFAULT != 'QCOM':
      raise RuntimeError('Jetlink camera adapter requires the native QCOM device')
    self.size = get_nv12_info(width, height)[3]
    self.transforms, self.inputs = input_buffers if input_buffers is not None else make_warp_inputs()
    self.blobs = {}
    self.timings = (0., 0., 0.)
    if prepared is not None:
      inputs_ready = time.monotonic()
      value = load_prepared(prepared)
      if not stream_abi():
        from tinygrad.engine.realize import lower_and_compile
        value['warp'].captured._linear = lower_and_compile(value['warp'].captured._linear)
      if value['identity'] != (width, height, self.size, SPEC.sha256):
        raise ValueError('prepared Jetlink warp contract mismatch')
      self.run_warp = value['warp']
      self.init_timings = (inputs_ready - started, time.monotonic() - inputs_ready, time.thread_time() - cpu_started)
      return
    from tinygrad import Tensor, TinyJit
    # The C4 launcher symlinks tinygrad, but not its examples package.
    # This import runs only in the isolated offroad preparation subprocess.
    import sys
    tinygrad_root = str(Path(__file__).resolve().parents[4] / 'tinygrad_repo')
    if tinygrad_root not in sys.path:
      sys.path.append(tinygrad_root)
    from tinygrad_repo.examples.openpilot.compile_warp import NV12Frame, make_frame_prepare
    warp = make_frame_prepare(NV12Frame(width, height, *get_nv12_info(width, height)), 512, 256)
    self.run_warp = TinyJit(lambda tfm, big_tfm, frame, big_frame: warp(frame, tfm).stack(warp(big_frame, big_tfm)))
    dummy = {k: Tensor(np.zeros(self.size, np.uint8), device='QCOM').realize() for k in ('frame', 'big_frame')}
    for _ in range(3):
      result = self.run_warp(**self.inputs, **dummy).numpy()
      if result.shape != SPEC.warped_shape or result.dtype != np.uint8:
        raise ValueError('unexpected camera warp contract')
  def save_prepared(self, path, width, height):
    from tinygrad import Tensor
    save_prepared({'identity': (width, height, self.size, SPEC.sha256), 'warp': self.run_warp}, path)
    # Verify serialization before handing the executable to modeld.
    restored = Warp(width, height, path.read_bytes())
    rng = np.random.default_rng(7527)
    frames = {k: Tensor(rng.integers(0, 256, self.size, dtype=np.uint8), device='QCOM').realize() for k in ('frame', 'big_frame')}
    expected = self.run_warp(**self.inputs, **frames).numpy()
    for _ in range(3):
      actual = restored.run_warp(**restored.inputs, **frames).numpy()
      if actual.shape != expected.shape or actual.dtype != expected.dtype or not np.array_equal(actual, expected):
        raise ValueError('prepared Jetlink warp serialization changed pixels')

  def __call__(self, bufs, transforms):
    from tinygrad import Tensor
    started = time.monotonic()
    frames = {}
    for key, arg in (('img', 'frame'), ('big_img', 'big_frame')):
      source = np.frombuffer(bufs[key].data, np.uint8)
      if source.size != self.size:
        raise ValueError('camera layout changed')
      cache_key = (key, source.ctypes.data)
      if cache_key not in self.blobs:
        if len(self.blobs) >= 64:
          self.blobs.clear()
        self.blobs[cache_key] = Tensor.from_blob(source.ctypes.data, (self.size,), dtype='uint8', device='QCOM')
      frames[arg] = self.blobs[cache_key]
    self.transforms['tfm'][:] = transforms['img']
    self.transforms['big_tfm'][:] = transforms['big_img']
    prepared = time.monotonic()
    result = self.run_warp(**self.inputs, **frames)
    submitted = time.monotonic()
    output = result.numpy()
    self.timings = (prepared - started, submitted - prepared, time.monotonic() - submitted)
    return output


class JoiningModel:
  """Leave all control and message publication in sunnypilot's stock modeld.

  Admission and warp preparation happen while stopped/disengaged. Once joined,
  moving does not disconnect the model. A failed live session latches commIssue
  before local inference resumes and cannot rejoin until stopped/disengaged.
  """
  def __init__(self, small, width, height):
    from openpilot.selfdrive.modeld.parse_model_outputs import Parser
    self.small = small
    self.warp_size = (width, height)
    self.warp_inputs = make_warp_inputs()
    self.warp = self.preparation = self.connection = self.client = None
    self.parser = Parser()
    self.active = self.ready = self.join_allowed = False
    self.small_runs = 0
    self.next_join = self.next_status = 0.
    self.error = ''
    self.frame = 0
    self.reset = True
    self.spec = SPEC
    self.packed = np.zeros(self.spec.packed_nelem, np.float32)
    self.views = {name: a.reshape(shape) for (name, shape), a in zip(
      SPEC.packed_shapes.items(), np.split(self.packed, np.cumsum(SPEC.packed_sizes[:-1])), strict=True)}
    self.prev_desire = np.zeros(8, np.float32)

  @property
  def lat_smooth_seconds(self):
    # Official CTV3M catalog override; applies only while V3 is driving.
    return .1 if self.active and self.spec.sha256 == V3_SPEC.sha256 else 0.

  def update(self, sm, metadata):
    now = time.monotonic()
    self.join_allowed = may_join(now, {k: sm[k] for k in ('carState', 'selfdriveState', 'carControl')},
                                {k: sm.valid[k] and sm.alive[k] for k in ('carState', 'selfdriveState', 'carControl')}, sm.recv_time)
    if self.join_allowed:
      FAULT.unlink(missing_ok=True)
    self.ready = state().get('state') == 'ready'
    if self.connection is not None and not (self.ready and self.join_allowed):
      self.connection.close()
      self.connection = None
    if now >= self.next_status:
      tmp = MODEL_STATUS.with_suffix('.tmp')
      tmp.write_text(json.dumps(dict(state='active' if self.active else 'local', active=self.active,
                                     ready=self.ready, error=self.error, model=model_name(self.spec),
                                     sha256=self.spec.sha256, updated=now)))
      os.replace(tmp, MODEL_STATUS)
      self.next_status = now + 1

  def _reset_small(self):
    self.small.prev_desire[:] = 0
    for array in self.small.npy.values():
      array[:] = 0
    for name, tensor in self.small.input_queues.items():
      if name.endswith('_q') or name in getattr(self.small, 'state_pairs', {}):
        tensor._buffer().copyin(memoryview(bytearray(tensor.numel() * tensor.dtype.itemsize)))

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    from openpilot.common.swaglog import cloudlog
    if self.preparation is not None and not self.ready:
      self.preparation.close()
      self.preparation = None
    if self.warp is None and self.small_runs >= 3 and self.ready and self.join_allowed and time.monotonic() >= self.next_join:
      try:
        if self.preparation is None:
          self.preparation = WarpPreparation(*self.warp_size)
        if self.preparation.future.done():
          prepared, _ = self.preparation.future.result()
          self.warp = Warp(*self.warp_size, prepared=prepared, input_buffers=self.warp_inputs)
          self.preparation = None
      except Exception as exc:
        cloudlog.exception('Jetlink camera adapter unavailable; retaining local model')
        if self.preparation is not None:
          self.preparation.close()
          self.preparation = None
        self.error = str(exc)
        self.next_join = time.monotonic() + 30
    if self.warp is not None and self.small_runs >= 3 and self.client is None and self.ready and self.join_allowed and time.monotonic() >= self.next_join:
      try:
        if self.connection is None:
          self.connection = ClientConnection()
        if self.connection.future.done():
          client = self.connection.future.result()
          self.connection = None
          self.client = client
          self.spec = client.spec
          self.packed = np.zeros(self.spec.packed_nelem, np.float32)
          self.views = {name: a.reshape(shape) for (name, shape), a in zip(
            self.spec.packed_shapes.items(), np.split(self.packed, np.cumsum(self.spec.packed_sizes[:-1])), strict=True)}
          self.prev_desire[:] = 0
          self.reset = True
      except Exception as exc:
        self.connection = None
        self.error = str(exc)
        self.next_join = time.monotonic() + 5
    if self.client is not None:
      try:
        desire = inputs['desire_pulse'].copy()
        desire[0] = 0
        self.views['desire'][:] = np.where(desire - self.prev_desire > .99, desire, 0)
        self.prev_desire[:] = desire
        for name in ('traffic_convention', 'action_t'):
          self.views[name][:] = inputs[name]
        self.frame = (self.frame + 1) & 0xFFFFFFFF
        images = self.warp(bufs, transforms)
        result = self.client.infer(images, self.packed, self.frame, self.reset)
        outputs = parse_outputs(self.parser, self.spec, result)
        if 'prev_feat' in self.views:
          self.views['prev_feat'][:] = result[self.spec.output_slices['hidden_state']]
        self.reset = False
        if not self.active:
          cloudlog.warning('Jetlink active: %s %s', model_name(self.spec), self.spec.sha256)
          self.error = ''
          self.next_status = 0
        self.active = True
        if os.getenv('SEND_RAW_PRED'):
          outputs['raw_pred'] = result.copy()
        return outputs
      except Exception as exc:
        cloudlog.exception('Jetlink failed; requesting disengagement before local recovery')
        # Latch even if the first remote frame fails: the native recurrence may
        # have been paused for a handshake and must not be assumed continuous.
        FAULT.write_text(str(time.monotonic()))
        self.error = str(exc)
        self.client.close()
        self.client = None
        self.active = False
        self._reset_small()
        self.small_runs = 0
        self.next_join = time.monotonic() + 5
        self.next_status = 0
        return None
    result = self.small.run(bufs, transforms, inputs, after_enqueue)
    if result is not None:
      self.small_runs += 1
    return result
