"""Native camera warp and a guarded switch between local and Jetlink inference."""
import json
import os
from pathlib import Path
import time

import numpy as np

from openpilot.selfdrive.modeld.jetlink.link import AsyncClient, ClientConnection, SPEC, FAULT, state
from openpilot.selfdrive.modeld.jetlink.recovery import Recovery, HOLD_FRAME, DROP_LIMIT
from openpilot.selfdrive.modeld.jetlink.contracts import model_name, parse_outputs, V3_SPEC
from openpilot.selfdrive.modeld.jetlink.gate import may_join
from openpilot.selfdrive.modeld.jetlink.artifact import load_prepared, save_prepared, stream_abi
from openpilot.selfdrive.modeld.jetlink.warp_runtime import make_coherent, prepare_small_reset

from openpilot.common.jetlink_status import MODEL_STATUS


def retime_inputs(inputs, old_smoothing, new_smoothing):
  if old_smoothing == new_smoothing:
    return inputs
  result = dict(inputs)
  result['action_t'] = inputs['action_t'].copy()
  result['action_t'][0] += new_smoothing - old_smoothing
  return result


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
      self._prepare_output()
      self.init_timings = (inputs_ready - started, time.monotonic() - inputs_ready, time.thread_time() - cpu_started)
      return
    from tinygrad import Tensor, TinyJit
    # The C4 launcher symlinks tinygrad, but not its examples package.
    # Import/compile before modeld enters its live frame loop.
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
    self._prepare_output()

  def _prepare_output(self):
    from tinygrad import Tensor, Device
    self._device = Device['QCOM']
    captured = self.run_warp.captured
    expected = ['big_frame', 'big_tfm', 'frame', 'tfm']
    if list(captured.expected_names) != expected:
      raise ValueError('HKG warp input ABI changed')
    out = captured.ret._buffer()
    if out.device != 'QCOM' or out.nbytes != SPEC.warped_nbytes:
      raise ValueError('HKG warp output ABI changed')
    make_coherent(out, self._device)
    # This fork already warmed the JIT. Relink after moving its output, before
    # any live frame; upstream moves it before its first link instead.
    captured.__dict__.pop('linear', None)
    dummy = {k: Tensor(np.zeros(self.size, np.uint8), device='QCOM').realize() for k in ('frame', 'big_frame')}
    for _ in range(2):
      self.run_warp(**self.inputs, **dummy)
    self._device.synchronize()
    self._output_array = np.frombuffer(out.as_memoryview(allow_zero_copy=True), np.uint8).reshape(SPEC.warped_shape)
    self._bindings = {}
    self._input_names = expected

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
    self.start(bufs, transforms)
    return self.wait()

  def start(self, bufs, transforms):
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
          self._bindings.clear()
        self.blobs[cache_key] = Tensor.from_blob(source.ctypes.data, (self.size,), dtype='uint8', device='QCOM')
      frames[arg] = self.blobs[cache_key]
    self.transforms['tfm'][:] = transforms['img']
    self.transforms['big_tfm'][:] = transforms['big_img']
    prepared = time.monotonic()
    binding = tuple(frames[k].uop.base for k in ('big_frame', 'frame'))
    if binding not in self._bindings:
      self._output = self.run_warp(**self.inputs, **frames)  # check each new camera buffer once
      args = dict(self.inputs, **frames)
      if len(self._bindings) >= 64:
        self._bindings.clear()
      self._bindings[binding] = [args[name].uop.base for name in self._input_names]
    else:
      self._output = self.run_warp.captured(self._bindings[binding], {})
    submitted = time.monotonic()
    self._times = (started, prepared, submitted)

  def wait(self):
    self._device.synchronize()
    output = self._output_array
    started, prepared, submitted = self._times
    self.timings = (prepared - started, submitted - prepared, time.monotonic() - submitted)
    return output


class JoiningModel:
  """Leave all control and message publication in sunnypilot's stock modeld.

  Warp preparation finishes before the live frame loop, on modeld's thread.
  Admission requires all controls off.
  A failed remote session returns a validated local result on the same frame.
  Only failed local recovery latches commIssue; a remote rejoin still proves
  its first 20 frames with engagement blocked.
  """
  def __init__(self, small, width, height):
    from openpilot.selfdrive.modeld.parse_model_outputs import Parser
    self.small = small
    self.warp_size = (width, height)
    self.warp_inputs = make_warp_inputs()
    self.warp = self.connection = self.client = None
    self.parser = Parser()
    self.active = self.ready = self.join_allowed = False
    self.small_runs = 0
    self.recovery = Recovery()
    self.last_output = None
    self.last_output_at = 0.
    self.frame_drop_ratio = 0.
    self.handovers = 0
    self.next_join = self.next_status = 0.
    self.error = ''
    self.fallback_until = 0.
    self.fallback_count = 0
    self.handover_at = 0.
    self.prepare_error = ''
    self.recovery_error = ''
    self.inference_ms = 0.
    self.last_frame = {}
    self.frame = 0
    self.reset = True
    self.spec = SPEC
    self.packed = np.zeros(self.spec.packed_nelem, np.float32)
    self.views = {name: a.reshape(shape) for (name, shape), a in zip(
      SPEC.packed_shapes.items(), np.split(self.packed, np.cumsum(SPEC.packed_sizes[:-1])), strict=True)}
    self.prev_desire = np.zeros(8, np.float32)
    self.reset_small = None

  def prepare(self):
    """Never compile or warm a second QCOM program beside live inference."""
    from openpilot.common.swaglog import cloudlog
    self._publish_status(phase='preparing')
    try:
      self.reset_small = prepare_small_reset(self.small)
      self.warp = Warp(*self.warp_size, input_buffers=self.warp_inputs)
    except Exception as exc:
      self.prepare_error = str(exc)
      self.error = 'Jetlink camera preparation failed; restart to retry: ' + str(exc)
      cloudlog.exception('Jetlink camera adapter unavailable; retaining local model')
    self._publish_status()
    return self

  def _phase(self):
    if self.recovery_error:
      return 'local_failed'
    if self.prepare_error:
      return 'prepare_failed'
    if self.warp is None:
      return 'not_prepared'
    if self.recovery.proving:
      return 'proving'
    if self.active:
      return 'active'
    if not self.ready:
      return 'waiting_link'
    if not self.join_allowed:
      return 'waiting_controls'
    if self.small_runs < 3:
      return 'warming_local'
    return 'retry_backoff' if time.monotonic() < self.next_join else 'connecting'

  def _publish_status(self, phase=None):
    now = time.monotonic()
    tmp = MODEL_STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(updated=now, state='active' if self.active else 'local',
                                  active=self.active, ready=self.ready, error=self.error,
                                  recovery_error=self.recovery_error,
                                  phase=phase or self._phase(), handover_at=self.handover_at,
                                  inference_ms=self.inference_ms,
                                  last_frame=self.last_frame,
                                  failures=self.recovery.failures, held=self.recovery.consecutive,
                                  fallback_until=self.fallback_until, fallback_count=self.fallback_count,
                                  proving=self.recovery.proving, model=model_name(self.spec), sha256=self.spec.sha256)))
    os.replace(tmp, MODEL_STATUS)
    self.next_status = now + 1

  @property
  def lat_smooth_seconds(self):
    # Official CTV3M catalog override; applies only while V3 is driving.
    return .1 if self.active and self.spec.sha256 == V3_SPEC.sha256 else 0.

  def update(self, sm, metadata):
    now = time.monotonic()
    required = ('carState', 'selfdriveState', 'carControl', 'carControlSP')
    self.join_allowed = may_join(now, {k: sm[k] for k in required},
                                {k: sm.valid[k] and sm.alive[k] for k in required}, sm.recv_time)
    if self.join_allowed and self.small_runs >= 3 and not self.recovery.proving:
      FAULT.unlink(missing_ok=True)
    self.ready = state().get('state') == 'ready'
    if self.connection is not None and not (self.ready and self.join_allowed):
      self.connection.close()
      self.connection = None
    if now >= self.next_status:
      self._publish_status()

  def _write_proving(self):
    # Publish the no-entry gate before the first remote inference. Failure to
    # publish aborts admission rather than allowing an unproven model.
    self._publish_status()
    self.next_status = 0

  def _remember(self, result):
    self.last_output = result.copy()
    self.last_output_at = (self.client.output_at if isinstance(self.client, AsyncClient) or
                           getattr(self.client, 'pipeline', False) is True else time.monotonic())
    if 'prev_feat' in self.views:
      self.views['prev_feat'][:] = result[self.spec.output_slices['hidden_state']]

  def _reset_small(self):
    if getattr(self, 'reset_small', None) is not None:
      self.reset_small()
      return
    self.small.prev_desire[:] = 0
    for array in self.small.npy.values():
      array[:] = 0
    for name, tensor in self.small.input_queues.items():
      if name.endswith('_q') or name in getattr(self.small, 'state_pairs', {}):
        from tinygrad.device import Buffer
        # HKG tinygrad 69fd4d3 has no Buffer.copyin(). Keep the existing
        # allocation: recurrent outputs and captured JITs alias these buffers.
        target = tensor._buffer()
        target.copy_from(Buffer('NPY', target.nbytes, opaque=memoryview(bytearray(target.nbytes))))

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    from openpilot.common.swaglog import cloudlog
    frame_smoothing = self.lat_smooth_seconds
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
          self.last_output = None
          self.handovers += 1
          self.recovery.joined(time.monotonic())
          self.fallback_until = 0.
          self._write_proving()
      except Exception as exc:
        if self.client is not None:
          self.client.close()
          self.client = None
        self.connection = None
        self.error = str(exc)
        self.next_join = time.monotonic() + self.recovery.failed(time.monotonic())
    if self.client is not None:
      try:
        if self.recovery.proving and not self.join_allowed:
          raise RuntimeError('Controls enabled during Jetlink proving')
        if self.active and self.frame_drop_ratio > DROP_LIMIT:
          raise TimeoutError('Jetlink camera frame drop ratio exceeded')
        started = time.monotonic()
        split_warp = hasattr(self.warp, 'start')
        if split_warp:
          self.warp.start(bufs, transforms)
        completed = None
        self.last_frame = dict(proving_frame=self.recovery.frames + 1)
        if isinstance(self.client, AsyncClient) or getattr(self.client, 'pipeline', False) is True:
          completed = self.client.take_ready()
          if completed is not None:
            self._remember(completed)
        desire = inputs['desire_pulse'].copy()
        desire[0] = 0
        self.views['desire'][:] = np.where(desire - self.prev_desire > .99, desire, 0)
        self.prev_desire[:] = desire
        remote_smoothing = .1 if self.spec.sha256 == V3_SPEC.sha256 else 0.
        remote_inputs = retime_inputs(inputs, frame_smoothing, remote_smoothing)
        for name in ('traffic_convention', 'action_t'):
          self.views[name][:] = remote_inputs[name]
        self.frame = (self.frame + 1) & 0xFFFFFFFF
        images = self.warp.wait() if split_warp else self.warp(bufs, transforms)
        warped_at = time.monotonic()
        try:
          if getattr(self.client, 'pipeline', False) is True:
            result = self.client.infer_frame(images, self.packed, self.frame, self.reset,
                                             started + HOLD_FRAME, after_enqueue)
            # A late earlier reply can arrive while waiting for this frame.
            # Keep its fresh output for a hold, as zoompilot's last_output does.
            if result is None:
              completed = self.client.take_ready()
              if completed is not None:
                self._remember(completed)
          elif isinstance(self.client, AsyncClient):
            result = self.client.infer(images, self.packed, self.frame, self.reset, deadline=started + HOLD_FRAME)
          else:
            result = self.client.infer(images, self.packed, self.frame, self.reset)
        finally:
          done = time.monotonic()
          self.last_frame.update(frame=self.frame, warp_pack_ms=(warped_at - started) * 1000.,
                                 ipc_wait_ms=(done - warped_at) * 1000., total_ms=(done - started) * 1000.,
                                 drained_reply=completed is not None)
        held = result is None
        if held:
          if self.last_output is None:
            raise TimeoutError('No Jetlink output available to hold')
          result = self.last_output.copy()
        else:
          self._remember(result)
        self.last_frame.update(held=held, output_age_ms=(time.monotonic() - self.last_output_at) * 1000.)
        if isinstance(self.client, AsyncClient):
          self.last_frame['usb_direct'] = getattr(self.client.client, 'metrics', {})
        elif getattr(self.client, 'pipeline', False) is True:
          self.last_frame['usb_direct'] = self.client.metrics
        was_proving = self.recovery.proving
        self.recovery.note(held, time.monotonic(), self.last_output_at)
        if was_proving and not self.recovery.proving:
          cloudlog.warning('Jetlink proving passed: %s %s', model_name(self.spec), self.spec.sha256)
          self._write_proving()
          if self.join_allowed:
            FAULT.unlink(missing_ok=True)
        outputs = parse_outputs(self.parser, self.spec, result)
        if not held and 'prev_feat' in self.views:
          self.views['prev_feat'][:] = result[self.spec.output_slices['hidden_state']]
        self.reset = False
        self.inference_ms = (time.monotonic() - started) * 1000.
        if not self.active:
          cloudlog.warning('Jetlink proving started: %s %s', model_name(self.spec), self.spec.sha256)
          self.error = ''
          self.next_status = 0
          self.handover_at = time.monotonic()
        self.active = True
        if self.next_status == 0:
          self._publish_status()
        if os.getenv('SEND_RAW_PRED'):
          outputs['raw_pred'] = result.copy()
        return outputs
      except Exception as exc:
        cloudlog.exception('Jetlink failed; attempting same-frame local recovery: %s', json.dumps(self.last_frame))
        self.error = str(exc)
        try:
          self.client.close()
        except Exception:
          cloudlog.exception('Jetlink client close failed during local recovery')
        self.client = None
        self.active = False
        self.handovers += 1
        self.last_output = None
        self.small_runs = 0
        self.next_join = time.monotonic() + self.recovery.failed(time.monotonic())
        self.next_status = 0
        try:
          self._reset_small()
          result = self._run_local(bufs, transforms, retime_inputs(inputs, frame_smoothing, 0.), None)
        except Exception as recovery_exc:
          # A reset failure is as fatal as a failed local inference. Never
          # announce recovery before a usable native result exists.
          self.fallback_until = 0.
          self.recovery_error = f'{type(recovery_exc).__name__}: {recovery_exc}'
          FAULT.write_text(str(time.monotonic()))
          self._write_proving()
          cloudlog.exception('Jetlink local recovery failed; modeld cannot continue')
          raise
        # Do not inject a synthetic commIssue just because the remote left.
        # The normal CAN, process, model freshness and localization checks
        # remain authoritative and can still disengage independently.
        self.fallback_count += 1
        self.handover_at = time.monotonic()
        self.fallback_until = time.monotonic() + 5.
        self._write_proving()
        cloudlog.warning('Jetlink fallback: small model recovered on this frame')
        return result
    return self._run_local(bufs, transforms, inputs, after_enqueue)

  def _run_local(self, bufs, transforms, inputs, after_enqueue):
    try:
      result = self.small.run(bufs, transforms, inputs, after_enqueue)
      if not isinstance(result, dict) or not result:
        raise RuntimeError('Local model did not return an output')
      if any(not np.all(np.isfinite(value)) for value in result.values()):
        raise ValueError('Local model output not finite')
    except Exception as exc:
      self.fallback_until = 0.
      self.recovery_error = f'{type(exc).__name__}: {exc}'
      FAULT.write_text(str(time.monotonic()))
      self._write_proving()
      raise
    self.small_runs += 1
    return result
