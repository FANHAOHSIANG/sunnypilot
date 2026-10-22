"""Wire-level delayed replies, backpressure and integrity, without a USB device."""
from collections import deque
import json
import struct
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from jetlink import protocol as P
from jetlink.transport.base import Message, LinkError, LinkTimeout
from openpilot.selfdrive.modeld.jetlink.contracts import DEFAULT_SPEC, V3_SPEC
from openpilot.selfdrive.modeld.jetlink.pipeline import PipelineClient
from openpilot.selfdrive.modeld.jetlink.direct import DirectClient


class Wire:
  protocol_version = 3
  def __init__(self):
    self.replies, self.sent, self.events = deque(), [], []
    self.busy = False
    self.last_send = self.last_receive = {}

  def send(self, kind, seq, parts=(), timeout=None, flags=0):
    self.sent.append((kind, seq, b''.join(parts)))
    self.events.append('send')

  def try_send(self, *args, **kwargs):
    if self.busy:
      return False
    self.send(*args, **kwargs)
    return True

  def recv(self, timeout=None):
    self.events.append('recv')
    if not self.replies:
      raise LinkTimeout('not yet')
    return self.replies.popleft()

  def reply(self, seq, frame, value=1., sha=V3_SPEC.sha256, status=0):
    output = np.full(V3_SPEC.output_nelem, value, np.float32)
    request = next((body for kind, sent, body in self.sent if kind == P.Msg.INFER_REQ and sent == seq), None)
    flags = P.unpack_infer_req(request)[1] if request is not None else P.Flag.WANT_STATE
    if not flags & P.Flag.WANT_HIDDEN:
      hidden = V3_SPEC.output_slices['hidden_state']
      output = np.concatenate((output[:hidden.start], output[hidden.stop:]))
    payload = struct.pack('<IIIII', frame, status, 32000, 50, 32050) + output.tobytes()
    if sha is not None:
      payload += json.dumps({'loaded': sha}).encode()
    self.replies.append(Message(P.Msg.INFER_RESP, seq, 0, memoryview(payload)))


class PipelineTests(unittest.TestCase):
  def setUp(self):
    clock = patch('time.monotonic', return_value=10.)
    self.clock = clock.start()
    self.addCleanup(clock.stop)
    self.wire = Wire()
    self.client = PipelineClient(self.wire, deadline=.15)
    self.client.spec = V3_SPEC
    self.image = np.zeros(V3_SPEC.warped_shape, np.uint8)
    self.packed = np.zeros(V3_SPEC.packed_nelem, np.float32)

  def send(self, frame, **kwargs):
    return self.client.infer_begin(self.image, self.packed, frame, **kwargs)

  def test_soft_timeout_keeps_request_and_allows_next_camera_frame(self):
    first = self.send(1, reset=True)
    self.assertIsNone(self.client.infer_end(first, hold=.046))
    self.clock.return_value = 10.05
    second = self.send(2, skip_if_busy=True)
    self.assertEqual(len(self.client.pending), 2)
    self.wire.reply(first, 1, value=1.)
    self.wire.reply(second, 2, value=2.)
    output = self.client.infer_end(second, hold=.04)
    expected = np.full(V3_SPEC.output_nelem, 2.)
    expected[V3_SPEC.output_slices['hidden_state']] = 0
    np.testing.assert_array_equal(output, expected)
    self.assertEqual(self.client.output_at, 10.05)
    self.assertEqual(len(self.client.pending), 0)
    self.assertFalse(self.client.dead)

  def test_drain_keeps_newest_late_reply_and_records_lost_sequence(self):
    self.send(1)
    second = self.send(2)
    self.wire.reply(second, 2)
    self.assertEqual(self.client.drain(), 1)
    self.assertEqual(self.client.output_frame, 2)
    self.assertEqual(self.client.frames_lost, 1)

  def test_compact_reply_preserves_slices_on_both_sides_of_hidden_state(self):
    seq = self.send(1)
    self.assertFalse(self.client.pending[0].flags & P.Flag.WANT_HIDDEN)
    self.wire.reply(seq, 1, value=7.)
    output = self.client.infer_end(seq)
    hidden = V3_SPEC.output_slices['hidden_state']
    np.testing.assert_array_equal(output[:hidden.start], 7.)
    np.testing.assert_array_equal(output[hidden], 0.)
    np.testing.assert_array_equal(output[hidden.stop:], 7.)
    self.assertEqual(output.size, V3_SPEC.output_nelem)

  def test_raw_predictions_request_and_receive_full_hidden_state(self):
    self.client.want_hidden = True
    seq = self.send(1)
    self.assertTrue(self.client.pending[0].flags & P.Flag.WANT_HIDDEN)
    self.wire.reply(seq, 1, value=7.)
    np.testing.assert_array_equal(self.client.infer_end(seq), 7.)

  def test_queued_model_keeps_recurrence_on_server_and_preserves_reset(self):
    self.client.spec = DEFAULT_SPEC
    packed = np.ones(DEFAULT_SPEC.packed_nelem, np.float32)
    seq = self.client.infer_begin(self.image, packed, 1, reset=True)
    body = self.wire.sent[-1][2]
    _, flags = P.unpack_infer_req(body)
    self.assertTrue(flags & P.Flag.RESET_QUEUES)
    self.assertFalse(flags & P.Flag.WANT_HIDDEN)
    # Only desire, traffic convention and action timing cross the wire;
    # prev_feat is owned by protocol 3's server, even for a queued graph.
    self.assertEqual(len(body), P.INFER_REQ_SIZE + DEFAULT_SPEC.warped_nbytes + 12 * 4)
    self.wire.reply(seq, 1, value=3., sha=DEFAULT_SPEC.sha256)
    output = self.client.infer_end(seq)
    np.testing.assert_array_equal(output[DEFAULT_SPEC.output_slices['hidden_state']], 0.)
    np.testing.assert_array_equal(output[DEFAULT_SPEC.output_slices['action']], 3.)

  def test_server_sending_full_output_to_compact_request_is_rejected(self):
    seq = self.send(1)
    payload = struct.pack('<IIIII', 1, 0, 32000, 50, 32050)
    payload += np.zeros(V3_SPEC.output_nelem, np.float32).tobytes()
    self.wire.replies.append(Message(P.Msg.INFER_RESP, seq, 0, memoryview(payload)))
    with self.assertRaises((ValueError, LinkError)):
      self.client.infer_end(seq)
    self.assertTrue(self.client.dead)

  def test_backpressure_refuses_without_queuing_or_resetting_history(self):
    self.wire.busy = True
    self.assertIsNone(self.send(1, skip_if_busy=True))
    self.assertEqual(len(self.client.pending), 0)
    self.assertEqual(self.wire.sent, [])
    self.assertFalse(self.client.dead)

  def test_zero_budget_still_accepts_already_arrived_reply(self):
    seq = self.send(1)
    self.wire.reply(seq, 1)
    self.assertIsNotNone(self.client.infer_end(seq, hold=0.))

  def test_oldest_unanswered_request_bounds_next_send(self):
    self.send(1)
    self.clock.return_value = 10.16
    with self.assertRaisesRegex(LinkError, 'deadline'):
      self.send(2)
    self.assertEqual(len(self.wire.sent), 1)

  def test_hard_deadline_is_not_extended_by_soft_holds(self):
    seq = self.send(1)
    self.clock.return_value = 10.16
    with self.assertRaisesRegex(LinkError, 'deadline'):
      self.client.infer_end(seq, hold=.046)

  def test_wrong_model_nonfinite_wrong_frame_and_duplicate_fail_closed(self):
    for options in ({'sha': '0' * 64}, {'value': float('nan')}, {'frame': 99}, {'status': 2}):
      with self.subTest(options=options):
        client = PipelineClient(self.wire, deadline=.15)
        client.spec = V3_SPEC
        seq = client.infer_begin(self.image, self.packed, 1)
        self.wire.reply(seq, **dict(frame=1, **options) if 'frame' not in options else options)
        with self.assertRaises(LinkError):
          client.infer_end(seq)
        self.assertTrue(client.dead)
    seq = self.send(2)
    self.wire.reply(seq + 50, 2)
    with self.assertRaises(LinkError):
      self.client.drain()

  def direct(self):
    direct = object.__new__(DirectClient)
    direct.client, direct.spec, direct.timeout = self.client, V3_SPEC, .15
    direct._closed = threading.Event()
    direct._io_lock = threading.Lock()
    direct._verified_frame = None
    direct.output_at, direct.sent = 0., False
    return direct

  def test_direct_send_then_health_callback_then_wait_without_worker_copy(self):
    direct = self.direct()
    self.wire.reply(1, 5)
    output = direct.infer_frame(self.image, self.packed, 5, True, 10.046,
                                lambda: self.wire.events.append('health'))
    self.assertEqual(self.wire.events, ['send', 'health', 'recv'])
    self.assertEqual(direct.output_at, 10.)
    output[:] = 99
    self.assertEqual(self.client.last_output[0], 1.)  # parser cannot corrupt a held raw output

  def test_late_reply_received_while_waiting_can_be_held_fresh(self):
    direct = self.direct()
    self.client.last_output = np.zeros(V3_SPEC.output_nelem, np.float32)
    first = self.send(1)
    self.wire.reply(first, 1)
    self.clock.return_value = 10.05
    self.assertIsNone(direct.infer_frame(self.image, self.packed, 2, False, 10.096))
    self.assertIsNotNone(direct.take_ready())
    self.assertEqual(direct.output_at, 10.)
    self.assertEqual(self.client.output_frame, 1)

  def test_legacy_telemetry_identity_query_has_remaining_hard_budget(self):
    direct = self.direct()
    seq = self.send(1)
    self.wire.reply(seq, 1, sha=None)
    self.clock.return_value = 10.1
    with patch.object(self.client, 'state', return_value={'loaded': V3_SPEC.sha256}) as state:
      self.assertIsNotNone(direct.take_ready())
      self.assertAlmostEqual(state.call_args.kwargs['timeout'], .05)

  def test_state_query_bounds_its_send_as_well_as_its_receive(self):
    self.wire.replies.append(Message(P.Msg.STATE_RESP, 1, 0, memoryview(b'{"loaded":"test"}')))
    with patch.object(self.wire, 'send', wraps=self.wire.send) as send:
      self.assertEqual(self.client.state(timeout=.03), {'loaded': 'test'})
      self.assertEqual(send.call_args.kwargs['timeout'], .03)

  def test_parser_cannot_turn_finite_raw_output_into_infinite_control_values(self):
    from openpilot.selfdrive.modeld.jetlink.contracts import parse_outputs
    parser = Mock()
    parser.parse_outputs.return_value = {'plan': np.array([float('inf')])}
    with self.assertRaisesRegex(ValueError, 'parsed output'):
      parse_outputs(parser, V3_SPEC, np.zeros(V3_SPEC.output_nelem, np.float32))


if __name__ == '__main__':
  unittest.main()
