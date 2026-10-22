"""Bounded USB pipeline adapted from zoompilot/jetlink b1d7041 client.py (MIT).

Copyright (c) 2026-, Zeph Leggett. See third_party/jetlink/LICENSE.

Keep this fork's protocol-3 packing and reconstruct its full-output contract
from zoompilot's compact replies (hidden state stays on the server). A soft wait
holds an output without discarding its pending reply; subsequent camera
frames can be submitted while an earlier reply is in flight. Only Mac USB
uses this class; the iOS socket adapter keeps its existing synchronous client.
"""
from collections import deque
from typing import NamedTuple
import json
import os
import time

import numpy as np

from jetlink import protocol as P
from jetlink.client import JetlinkClient, _as_bytes, _protocol3_packed
from jetlink.transport.base import LinkError, LinkTimeout


class Sent(NamedTuple):
  seq: int
  frame: int
  flags: int
  at: float


class PipelineClient(JetlinkClient):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.pending = deque()
    self.last_output = None
    self.output_at = 0.
    self.output_frame = None
    self.frames_lost = 0
    self.want_hidden = bool(os.getenv('SEND_RAW_PRED'))

  def _expect(self, kind, seq, timeout):
    end = None if timeout is None else time.monotonic() + timeout
    read_once_late = False
    while True:
      left = None if end is None else end - time.monotonic()
      if left is not None and left <= 0:
        if read_once_late:
          raise LinkTimeout('USB reply deadline elapsed')
        read_once_late, left = True, 0.
      message = self.t.recv(timeout=left)
      if message.msg_type == kind and message.seq == seq:
        return message
      if message.msg_type == P.Msg.INFER_RESP:
        self._take_reply(message)
      else:
        self._dispatch(message)

  def state(self, timeout=2.):
    started = time.monotonic()
    seq = self._next_seq()
    self.t.send(P.Msg.STATE_REQ, seq, (b'{}',), timeout=timeout)
    message = self._expect(P.Msg.STATE_RESP, seq, max(0., timeout - (time.monotonic() - started)))
    state = json.loads(bytes(message.payload))
    if not isinstance(state, dict):
      raise LinkError('Server state is not an object')
    return state

  def infer_begin(self, warped, packed, frame_id=0, reset=False, want_state=True, deadline=None, skip_if_busy=False):
    if self.spec is None or self.dead:
      raise LinkError('USB pipeline is not ready')
    if self._protocol_version() != 3:
      raise LinkError('USB pipeline requires protocol 3')
    if self.pending and time.monotonic() - self.pending[0].at >= self.deadline:
      self.dead = True
      raise LinkError('Jetlink inference deadline exceeded')
    # An additional memory bound beside the time limit and FunctionFS queue.
    # At 20 Hz the 150 ms hard deadline permits fewer than four live requests.
    if len(self.pending) >= 4:
      return None
    warped = _as_bytes(warped, self.spec.warped_nbytes, 'warped')
    packed = _protocol3_packed(self.spec, _as_bytes(packed, self.spec.packed_nbytes, 'packed'))
    seq = self._next_seq()
    flags = ((P.Flag.WANT_HIDDEN if self.want_hidden else 0) |
             (P.Flag.WANT_STATE if want_state else 0) | (P.Flag.RESET_QUEUES if reset else 0))
    parts = (P.pack_infer_req(frame_id, flags), warped, packed)
    sent = Sent(seq, frame_id, flags, time.monotonic())
    try:
      if skip_if_busy:
        if not self.t.try_send(P.Msg.INFER_REQ, seq, parts, timeout=self.deadline):
          return None
      else:
        self.t.send(P.Msg.INFER_REQ, seq, parts, timeout=self.deadline if deadline is None else deadline)
    except LinkError:
      self.dead = True
      raise
    self.pending.append(sent)
    return seq

  def infer_end(self, seq, deadline=None, hold=None):
    sent = next((f for f in self.pending if f.seq == seq), None)
    if sent is None:
      raise LinkError('USB response has no outstanding request')
    hard_left = (self.deadline if deadline is None else deadline) - (time.monotonic() - self.pending[0].at)
    if hard_left <= 0:
      self.dead = True
      raise LinkError('Jetlink inference deadline exceeded')
    wait = hard_left if hold is None else min(hard_left, max(hold, 0.))
    try:
      message = self._expect(P.Msg.INFER_RESP, seq, wait)
    except LinkTimeout:
      if hold is not None and time.monotonic() - self.pending[0].at < self.deadline:
        return None
      self.dead = True
      raise LinkError('Jetlink inference deadline exceeded') from None
    except LinkError:
      self.dead = True
      raise
    return self._take_reply(message)

  def drain(self):
    answered = 0
    while self.pending:
      try:
        message = self.t.recv(timeout=0.)
      except LinkTimeout:
        break
      if message.msg_type == P.Msg.INFER_RESP:
        self._take_reply(message)
        answered += 1
      else:
        self._dispatch(message)
    return answered

  def _take_reply(self, message):
    try:
      return self._checked_reply(message)
    except (LinkError, ValueError, TypeError):
      self.dead = True
      raise

  def _checked_reply(self, message):
    if not any(f.seq == message.seq for f in self.pending):
      raise LinkError('Unexpected or duplicate USB inference sequence')
    while self.pending[0].seq != message.seq:
      self.pending.popleft()
      self.frames_lost += 1
    sent = self.pending.popleft()
    if message.payload.nbytes < P.INFER_RESP_SIZE:
      raise LinkError('Inference response is missing its header')
    frame, status, gpu, queue, total = P.unpack_infer_resp(message.payload)
    if frame != sent.frame or status != P.Status.OK:
      raise LinkError('Inference response frame/status mismatch')
    hidden = self.spec.output_slices.get('hidden_state')
    hidden_range = hidden.indices(self.spec.output_nelem)[:2] if hidden is not None else None
    whole = bool(sent.flags & P.Flag.WANT_HIDDEN)
    count = self.spec.output_nelem
    if not whole and hidden_range is not None:
      count -= hidden_range[1] - hidden_range[0]
    end = P.INFER_RESP_SIZE + count * 4
    if message.payload.nbytes < end:
      raise LinkError('Inference response is missing model outputs')
    state = None
    if message.payload.nbytes > end:
      if not sent.flags & P.Flag.WANT_STATE:
        raise LinkError('Unexpected inference telemetry or output layout')
      state = json.loads(bytes(message.payload[end:]))
      if not isinstance(state, dict):
        raise LinkError('Inference telemetry is not an object')
      if 'loaded' in state and state['loaded'] != self.spec.sha256:
        raise LinkError('App model changed during USB inference')
    reply = np.frombuffer(message.payload, np.float32, count, P.INFER_RESP_SIZE)
    if not np.all(np.isfinite(reply)):
      raise LinkError('Inference output is not finite')
    if not whole and hidden_range is not None:
      # Protocol 3 owns recurrence on the server. Keep parser slice offsets
      # without bringing that state back over USB or feeding it to the graph.
      start, stop = hidden_range
      output = np.zeros(self.spec.output_nelem, np.float32)
      output[:start], output[stop:] = reply[:start], reply[start:]
    else:
      output = reply.copy()
    if state is not None:
      self.last_state = state
    self.last_timings = gpu, queue, total
    self.last_output, self.output_at, self.output_frame = output, sent.at, frame
    return output
