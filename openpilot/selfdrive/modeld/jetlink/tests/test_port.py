"""Hardware-independent admission, failure and wire-contract regression tests.

Run from the checkout root:
  python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -v
"""
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.common import jetlink_status
from openpilot.selfdrive.modeld.jetlink import VENDOR
from openpilot.selfdrive.modeld.jetlink import link, model
from openpilot.selfdrive.modeld.jetlink.contracts import DEFAULT_SPEC, validate_model_spec, parse_outputs
from openpilot.selfdrive.modeld.jetlink.gate import may_join
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from jetlink.spec import ModelSpec


class GateTests(unittest.TestCase):
  def setUp(self):
    self.messages = {
      'carState': SimpleNamespace(standstill=True, vEgo=0.),
      'selfdriveState': SimpleNamespace(enabled=False, active=False),
      'carControl': SimpleNamespace(enabled=False, latActive=False, longActive=False),
      'carControlSP': SimpleNamespace(mads=SimpleNamespace(enabled=False, active=False)),
    }
    self.valid = dict.fromkeys(self.messages, True)
    self.received = dict.fromkeys(self.messages, 9.9)

  def allowed(self):
    return may_join(10., self.messages, self.valid, self.received)

  def test_stationary_disengaged(self):
    self.assertTrue(self.allowed())

  def test_all_active_control_states_block(self):
    for channel, field in [('selfdriveState', 'enabled'), ('selfdriveState', 'active'),
                           ('carControl', 'enabled'), ('carControl', 'latActive'), ('carControl', 'longActive')]:
      with self.subTest(channel=channel, field=field):
        setattr(self.messages[channel], field, True)
        self.assertFalse(self.allowed())
        setattr(self.messages[channel], field, False)

  def test_motion_and_false_standstill_block(self):
    for standstill, speed in [(False, 0.), (True, .01), (True, -1.), (True, float('nan'))]:
      self.messages['carState'] = SimpleNamespace(standstill=standstill, vEgo=speed)
      self.assertFalse(self.allowed())

  def test_enabled_but_paused_mads_prevents_model_switch(self):
    self.messages['carControlSP'].mads.enabled = True
    self.messages['carControlSP'].mads.active = False
    self.assertFalse(self.allowed())

  def test_each_stale_invalid_missing_or_future_message_blocks(self):
    for channel in self.messages:
      with self.subTest(channel=channel):
        self.valid[channel] = False
        self.assertFalse(self.allowed())
        self.valid[channel] = True
        for timestamp in (9.75, 10.1, -1000):
          self.received[channel] = timestamp
          self.assertFalse(self.allowed())
        del self.received[channel]
        self.assertFalse(self.allowed())
        self.received[channel] = 9.9


class ContractTests(unittest.TestCase):
  def test_pinned_model_accepted(self):
    self.assertEqual(validate_model_spec(DEFAULT_SPEC), DEFAULT_SPEC)

  def test_every_identity_and_tensor_contract_field_checked(self):
    changes = {'sha256': 'invalid', 'nbytes': 1, 'frame_skip': 1, 'checkpoint': 'other',
               'input_shapes': {'img': [1, 12, 128, 256]}, 'output_shapes': {'outputs': [1, 1]},
               'output_slices': {'plan': [0, 990]}}
    for key, value in changes.items():
      with self.subTest(key=key):
        raw = DEFAULT_SPEC.to_dict()
        raw[key] = value
        with self.assertRaises(ValueError):
          validate_model_spec(ModelSpec.from_dict(raw))

  def test_real_sunnypilot_parser_output_shapes_and_finite_values(self):
    outputs = parse_outputs(Parser(), DEFAULT_SPEC, np.zeros(DEFAULT_SPEC.output_nelem, np.float32))
    for key, shape in {'plan': (1, 33, 15), 'pose': (1, 6), 'lane_lines': (1, 4, 33, 2),
                       'road_edges': (1, 2, 33, 2), 'desire_state': (1, 8), 'action': (1, 4)}.items():
      self.assertEqual(outputs[key].shape, shape)
      self.assertTrue(np.all(np.isfinite(outputs[key])))

  def test_vendor_resolves_inside_checkout(self):
    self.assertTrue((VENDOR / 'jetlink' / 'client.py').is_file())


class StatusTests(unittest.TestCase):
  def test_stale_corrupt_and_future_status_rejected(self):
    with tempfile.TemporaryDirectory() as directory:
      p = Path(directory) / 'status.json'
      for payload in ['bad json', '{}', '[]', '{"updated": 100.1}', '{"updated": 96}']:
        p.write_text(payload)
        self.assertEqual(jetlink_status.read_status(p, now=100), {'state': 'unavailable'})
      p.write_text('{"updated": 99.9, "state": "ready"}')
      self.assertEqual(jetlink_status.read_status(p, now=100)['state'], 'ready')

  def test_fault_does_not_expire_on_a_timer(self):
    with tempfile.TemporaryDirectory() as directory, patch.object(jetlink_status, 'FAULT', Path(directory) / 'fault'):
      jetlink_status.FAULT.write_text('0')
      self.assertTrue(jetlink_status.fault_active())


class IpcTests(unittest.TestCase):
  def test_fragmented_packet_roundtrip(self):
    left, right = socket.socketpair()
    with left, right:
      payload = b'packet' * 100
      def writer():
        packet = struct.pack('<I', len(payload)) + payload
        for at in range(0, len(packet), 3):
          left.sendall(packet[at:at+3])
      t = threading.Thread(target=writer)
      t.start()
      self.assertEqual(link.receive(right, time.monotonic() + 1), payload)
      t.join()

  def test_invalid_sizes_and_partial_disconnect(self):
    for size in (0, link.MAX_PACKET + 1):
      left, right = socket.socketpair()
      with left, right:
        left.sendall(struct.pack('<I', size))
        with self.assertRaises(ValueError):
          link.receive(right)
    left, right = socket.socketpair()
    with left, right:
      left.sendall(struct.pack('<I', 10) + b'a')
      left.shutdown(socket.SHUT_WR)
      with self.assertRaises(ConnectionError):
        link.receive(right)

  def exercise_client(self, frame_reply=7, nan=False, timeout=False, wrong_spec=False):
    with tempfile.TemporaryDirectory() as directory:
      path = str(Path(directory) / 'ipc.sock')
      server_socket, client_socket = socket.socketpair()
      # Exercise real kernel streams without filesystem socket creation,
      # which the CI sandbox may prohibit.
      class ConnectedSocket:
        def __getattr__(self, name):
          return getattr(client_socket, name)
        def connect(self, target):
          if target != path:
            raise AssertionError(target)
      errors = []
      def server():
        try:
          conn = server_socket
          with conn:
            spec = DEFAULT_SPEC.to_dict()
            if wrong_spec:
              spec['frame_skip'] = 1
            link.send(conn, json.dumps({'spec': spec}).encode())
            if wrong_spec:
              return
            request = link.receive(conn)
            self.assertEqual(len(request), link.REQUEST.size + DEFAULT_SPEC.warped_nbytes + DEFAULT_SPEC.packed_nbytes)
            self.assertEqual(link.REQUEST.unpack_from(request), (7, 1, 0))
            if timeout:
              # Wait for the client's deadline, without a clock-based sleep.
              conn.recv(1)
              return
            result = np.zeros(DEFAULT_SPEC.output_nelem, np.float32)
            if nan:
              result[0] = np.nan
            link.send_parts(conn, link.REPLY.pack(frame_reply, 1, 2, 3), result)
        except Exception as exc:
          errors.append(exc)
      t = threading.Thread(target=server)
      t.start()
      factory = patch.object(link.socket, "socket", return_value=ConnectedSocket())
      factory.start()
      try:
        if wrong_spec:
          with self.assertRaises(ValueError):
            link.Client(path=path)
        else:
          client = link.Client(path=path, timeout=.1)
          try:
            image = np.zeros(DEFAULT_SPEC.warped_shape, np.uint8)
            packed = np.zeros(DEFAULT_SPEC.packed_nelem, np.float32)
            if timeout:
              with self.assertRaises(TimeoutError):
                client.infer(image, packed, 7, reset=True)
            elif nan or frame_reply != 7:
              with self.assertRaises(ValueError):
                client.infer(image, packed, 7, reset=True)
            else:
              output = client.infer(image, packed, 7, reset=True)
              self.assertEqual(output.size, DEFAULT_SPEC.output_nelem)
              self.assertEqual(client.timings, [1, 2, 3])
          finally:
            client.close()
      finally:
        t.join(timeout=2)
        factory.stop()
        server_socket.close()
        client_socket.close()
      self.assertFalse(t.is_alive())
      if errors:
        raise errors[0]

  def test_success(self):
    self.exercise_client()

  def test_stale_frame_rejected(self):
    self.exercise_client(frame_reply=6)

  def test_nonfinite_reply_rejected(self):
    self.exercise_client(nan=True)

  def test_silent_peer_times_out(self):
    self.exercise_client(timeout=True)

  def test_mismatched_model_handshake_rejected(self):
    self.exercise_client(wrong_spec=True)


class ModelTransitionTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.fault = Path(self.directory.name) / 'fault'
    self.status = Path(self.directory.name) / 'status.json'
    self.patches = [patch.object(model, 'FAULT', self.fault), patch.object(model, 'MODEL_STATUS', self.status),
                    patch.object(model, 'make_warp_inputs', return_value=({}, {})),
                    patch.dict(sys.modules, {'openpilot.common.swaglog': SimpleNamespace(cloudlog=Mock())})]
    for p in self.patches:
      p.start()
      self.addCleanup(p.stop)
    self.local = SimpleNamespace(run=Mock(return_value={'local': True}), prev_desire=np.ones(8),
                                 npy={'desire': np.ones(8)}, input_queues={})
    self.adapter = model.JoiningModel(self.local, 1928, 1208)
    self.inputs = {'desire_pulse': np.zeros(8, np.float32), 'traffic_convention': np.array([1., 0.]),
                   'action_t': np.array([.2, .4])}

  def test_local_when_host_absent(self):
    for _ in range(4):
      self.assertEqual(self.adapter.run({}, {}, self.inputs), {'local': True})
    self.assertEqual(self.adapter.small_runs, 4)
    self.assertFalse(self.fault.exists())

  def test_never_connect_while_moving(self):
    self.adapter.warp = Mock()
    self.adapter.ready = True
    self.adapter.small_runs = 4
    self.adapter.join_allowed = False
    with patch.object(model, 'ClientConnection') as connect:
      self.adapter.run({}, {}, self.inputs)
      connect.assert_not_called()

  def test_live_failure_latches_fault_resets_local_and_requires_rewarm(self):
    self.adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = TimeoutError('no reply')
    self.adapter.active = True
    self.assertEqual(self.adapter.run({}, {}, self.inputs), {'local': True})
    self.assertTrue(self.fault.exists())
    self.assertFalse(self.adapter.active)
    self.assertIsNone(self.adapter.client)
    self.assertEqual(self.adapter.small_runs, 1)
    np.testing.assert_array_equal(self.local.prev_desire, np.zeros(8))
    self.local.run.assert_called_once_with({}, {}, self.inputs, None)
    self.adapter.run({}, {}, self.inputs)
    self.assertEqual(self.adapter.small_runs, 2)

  def test_first_remote_frame_failure_also_returns_native_output_and_latches_fault(self):
    self.adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = ValueError('invalid remote reply')
    self.assertEqual(self.adapter.run({}, {}, self.inputs), {'local': True})
    self.assertTrue(self.fault.exists())
    self.assertFalse(self.adapter.active)
    self.assertEqual(self.adapter.small_runs, 1)

  def test_socket_close_failure_does_not_prevent_native_fallback(self):
    self.adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = TimeoutError('no reply')
    self.adapter.client.close.side_effect = OSError('close failed')
    self.assertEqual(self.adapter.run({}, {}, self.inputs), {'local': True})
    self.assertTrue(self.fault.exists())
    self.assertIsNone(self.adapter.client)

  def test_failed_native_fallback_is_not_hidden(self):
    self.adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = TimeoutError('no reply')
    self.local.run.side_effect = RuntimeError('native model failed')
    with self.assertRaisesRegex(RuntimeError, 'native model failed'):
      self.adapter.run({}, {}, self.inputs)
    self.assertTrue(self.fault.exists())

  def test_after_failure_moving_does_not_rejoin_or_clear_fault(self):
    self.adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.side_effect = TimeoutError('no reply')
    self.adapter.join_allowed = False
    self.adapter.ready = True
    self.adapter.run({}, {}, self.inputs)
    self.adapter.next_join = 0
    with patch.object(model, 'ClientConnection') as connect:
      for _ in range(4):
        self.assertEqual(self.adapter.run({}, {}, self.inputs), {'local': True})
      connect.assert_not_called()
    self.assertTrue(self.fault.exists())

  def test_active_remote_continues_when_join_gate_closes(self):
    self.adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.return_value = np.zeros(DEFAULT_SPEC.output_nelem, np.float32)
    self.adapter.active = True
    self.adapter.join_allowed = False
    output = self.adapter.run({}, {}, self.inputs)
    self.assertIn('plan', output)
    self.assertTrue(self.adapter.active)
    self.local.run.assert_not_called()

  def test_fault_clears_only_with_fresh_stopped_disengaged_state(self):
    self.fault.write_text('0')
    messages = {'carState': SimpleNamespace(standstill=True, vEgo=0.),
                'selfdriveState': SimpleNamespace(enabled=False, active=False),
                'carControl': SimpleNamespace(enabled=False, latActive=False, longActive=False),
                'carControlSP': SimpleNamespace(mads=SimpleNamespace(enabled=False, active=False))}
    class State(dict):
      pass
    sm = State(messages)
    sm.valid = sm.alive = dict.fromkeys(messages, True)
    sm.recv_time = dict.fromkeys(messages, 0.)
    with patch.object(model, 'state', return_value={'state': 'unavailable'}):
      self.adapter.update(sm, None)
      self.assertTrue(self.fault.exists())
      sm.recv_time = dict.fromkeys(messages, time.monotonic())
      self.adapter.update(sm, None)
      self.assertFalse(self.fault.exists())


if __name__ == '__main__':
  unittest.main()
