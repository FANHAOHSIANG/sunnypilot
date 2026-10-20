"""Synthetic graph fixtures test format adaptation, not real model quality."""
from concurrent.futures import Future
from copy import deepcopy
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import test_port
from openpilot.selfdrive.modeld.jetlink import model
from openpilot.selfdrive.modeld.jetlink import daemon, link
from openpilot.selfdrive.modeld.jetlink.contracts import DEFAULT_SPEC, validate_model_spec, parse_outputs
from openpilot.selfdrive.modeld.jetlink.daemon import ModelChanged, prepare_selected, require_selected
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from jetlink.spec import ModelSpec


def graph(stateful=False, legacy=False):
  raw = deepcopy(DEFAULT_SPEC.to_dict())
  raw.update(sha256='a' * 64, checkpoint='synthetic-fixture', nbytes=1000)
  if stateful:
    raw['input_shapes'] = {'new_img': [2, 6, 128, 256], 'desire': [1, 8],
                           'traffic_convention': [1, 2], 'action_t': [1, 2], 'state_context': [1, 64]}
    raw['output_shapes']['next_state_context'] = [1, 64]
  else:
    raw['input_shapes']['features_buffer'] = [1, 16, 512]
    raw['input_shapes']['desire_pulse'] = [1, 17, 8]
  widths = {k: v[1] - v[0] for k, v in raw['output_slices'].items()}
  widths['hidden_state'] = 512
  if legacy:
    widths['plan'], widths['lead'] = 4955, 102
    del widths['action']
  offset = 0
  raw['output_slices'] = {}
  for name, width in widths.items():
    raw['output_slices'][name] = [offset, offset + width]
    offset += width
  raw['output_shapes']['outputs'] = [1, offset]
  return ModelSpec.from_dict(raw)


class GraphTests(unittest.TestCase):
  def test_different_model_id_with_compatible_contract_is_accepted(self):
    for stateful, legacy in [(False, False), (False, True), (True, False), (True, True)]:
      with self.subTest(stateful=stateful, legacy=legacy):
        spec = graph(stateful, legacy)
        self.assertIs(validate_model_spec(spec), spec)
        parsed = parse_outputs(Parser(), spec, np.zeros(spec.output_nelem, np.float32))
        self.assertEqual(parsed['plan'].shape, (1, 33, 15))
        self.assertEqual(parsed['lead'].shape, (1, 3, 6, 4))
        self.assertTrue(np.all(np.isfinite(parsed['plan'])))

  def test_incompatible_graphs_are_rejected(self):
    for changes in [
      {'input_shapes': {'img': [1, 6, 128, 256]}},
      {'frame_skip': 2},
      {'output_shapes': {'outputs': [1, 1000000]}},
      {'output_slices': {'plan': [0, 10]}},
    ]:
      raw = graph().to_dict()
      raw.update(changes)
      with self.subTest(changes=changes), self.assertRaises((ValueError, KeyError)):
        validate_model_spec(ModelSpec.from_dict(raw))

  def test_overlapping_outputs_and_wrong_hidden_width_rejected(self):
    for bad_slice in ([0, 512], [2066, 2067]):
      raw = graph().to_dict()
      raw['output_slices']['hidden_state'] = bad_slice
      with self.assertRaises(ValueError):
        validate_model_spec(ModelSpec.from_dict(raw))

  def test_stateful_state_pair_must_match_and_no_unknown_scalar_inputs(self):
    for name, shape in [('next_state_context', [1, 65]), ('extra', [1])]:
      raw = graph(stateful=True).to_dict()
      raw['output_shapes'][name] = shape
      with self.assertRaises(ValueError):
        validate_model_spec(ModelSpec.from_dict(raw))

  def test_known_sha_cannot_change_contract(self):
    raw = graph().to_dict()
    raw['sha256'] = DEFAULT_SPEC.sha256
    with self.assertRaises(ValueError):
      validate_model_spec(ModelSpec.from_dict(raw))


class SelectionTests(unittest.TestCase):
  def client(self, spec):
    client = Mock()
    client.ensure_engine.return_value = spec
    client.state.return_value = {'loaded': spec.sha256}
    return client

  def test_app_model_prepared_by_selected_sha(self):
    spec = graph()
    client = self.client(spec)
    self.assertIs(prepare_selected(client, {'protocol': 3, 'loaded': spec.sha256}, lambda: True), spec)
    args, kwargs = client.ensure_engine.call_args
    self.assertEqual(args, (spec.sha256, 0))
    self.assertEqual(kwargs['frame_skip'], 4)
    self.assertFalse(client._should_stop())

  def test_no_selection_or_wrong_protocol_rejected(self):
    for peer in [{'protocol': 3, 'loaded': None}, {'protocol': 2, 'loaded': 'a'*64},
                 {'protocol': True, 'loaded': 'a'*64}, {'protocol': 3, 'loaded': 'bad'}]:
      client = self.client(graph())
      with self.subTest(peer=peer), self.assertRaises(ValueError):
        prepare_selected(client, peer, lambda: True)
      client.ensure_engine.assert_not_called()

  def test_ignition_on_prevents_model_setup(self):
    spec = graph()
    client = self.client(spec)
    with self.assertRaises(RuntimeError):
      prepare_selected(client, {'protocol': 3, 'loaded': spec.sha256}, lambda: False)
    client.ensure_engine.assert_not_called()

  def test_ignition_started_during_setup_and_model_changed_both_rejected(self):
    spec = graph()
    client = self.client(spec)
    offroad = Mock(side_effect=[True, False])
    with self.assertRaises(RuntimeError):
      prepare_selected(client, {'protocol': 3, 'loaded': spec.sha256}, offroad)
    self.assertFalse(client._should_stop())
    client = self.client(spec)
    client.state.side_effect = [{'loaded': spec.sha256}, {'loaded': 'b'*64}]
    with self.assertRaises(ModelChanged):
      prepare_selected(client, {'protocol': 3, 'loaded': spec.sha256}, lambda: True)

  def test_incompatible_selected_graph_rejected_before_ready(self):
    raw = graph().to_dict()
    raw['frame_skip'] = 1
    spec = ModelSpec.from_dict(raw)
    client = self.client(spec)
    with self.assertRaises(ValueError):
      prepare_selected(client, {'protocol': 3, 'loaded': spec.sha256}, lambda: True)
    self.assertFalse(client._should_stop())

  def test_changed_or_missing_frame_identity_is_not_forwarded(self):
    spec = graph()
    for state in [None, {}, {'loaded': None}, {'loaded': 'b'*64}]:
      with self.subTest(state=state), self.assertRaises(ModelChanged):
        require_selected(state, spec)
    require_selected({'loaded': spec.sha256}, spec)


class DynamicModelTests(unittest.TestCase):
  setUp = test_port.ModelTransitionTests.setUp

  def test_dynamic_queued_and_stateful_handshake_allocate_matching_buffers(self):
    for stateful in (False, True):
      with self.subTest(stateful=stateful):
        adapter = model.JoiningModel(self.local, 1928, 1208)
        adapter.warp = Mock(return_value=np.zeros(DEFAULT_SPEC.warped_shape, np.uint8))
        adapter.ready = adapter.join_allowed = True
        adapter.small_runs = 4
        client = Mock()
        client.spec = graph(stateful=stateful)
        client.infer.return_value = np.zeros(client.spec.output_nelem, np.float32)
        future = Future()
        future.set_result(client)
        with patch.object(model, 'ClientConnection', return_value=SimpleNamespace(future=future)):
          parsed = adapter.run({}, {}, self.inputs)
        self.assertEqual(adapter.packed.size, client.spec.packed_nelem)
        self.assertEqual('prev_feat' in adapter.views, not stateful)
        self.assertEqual(parsed['plan'].shape, (1, 33, 15))
        self.assertTrue(client.infer.call_args.args[3])
        self.assertTrue(adapter.active)


class DaemonIpcTests(unittest.TestCase):
  def exercise(self, stateful, change_model=False):
    spec = graph(stateful)
    remote = Mock()
    remote.spec = spec
    remote.state.return_value = {'loaded': spec.sha256}
    remote.last_state = {'loaded': 'b'*64 if change_model else spec.sha256}
    remote.last_timings = (1, 2, 3)
    remote.infer.return_value = np.zeros(spec.output_nelem, np.float32)
    server, endpoint = socket.socketpair()
    listener = Mock()
    listener.accept.side_effect = [(server, None), ModelChanged('test session completed')]
    errors = []
    class ConnectedSocket:
      def connect(self, path):
        pass
      def __getattr__(self, key):
        return getattr(endpoint, key)
    def serve():
      try:
        daemon.serve_local(listener, remote)
      except ModelChanged:
        pass
      except Exception as exc:
        errors.append(exc)
    with patch.object(daemon, 'host_attached', return_value=True), patch.object(daemon, 'publish'), \
         patch.object(link.socket, 'socket', return_value=ConnectedSocket()):
      thread = threading.Thread(target=serve)
      thread.start()
      client = None
      try:
        client = link.Client(path='unused', timeout=.5)
        self.assertEqual(client.spec.to_dict(), spec.to_dict())
        image = np.zeros(spec.warped_shape, np.uint8)
        packed = np.zeros(spec.packed_nelem, np.float32)
        if change_model:
          with self.assertRaises(ConnectionError):
            client.infer(image, packed, 7, reset=True)
        else:
          result = client.infer(image, packed, 7, reset=True)
          self.assertEqual(result.size, spec.output_nelem)
        remote.infer.assert_called_once()
        args, kwargs = remote.infer.call_args
        self.assertEqual(args[1].size, spec.packed_nelem)
        self.assertTrue(kwargs['reset'])
        self.assertTrue(kwargs['want_state'])
      finally:
        if client is not None:
          client.close()
        endpoint.close()
        thread.join(timeout=2)
        server.close()
      self.assertFalse(thread.is_alive())
      if errors:
        raise errors[0]

  def test_dynamic_queued_daemon_and_local_client_roundtrip(self):
    self.exercise(stateful=False)

  def test_dynamic_stateful_daemon_and_local_client_roundtrip(self):
    self.exercise(stateful=True)

  def test_changed_model_reply_never_reaches_local_client(self):
    self.exercise(stateful=True, change_model=True)


if __name__ == '__main__':
  unittest.main()
