"""Recovery timing, admission proof and asynchronous IPC ownership."""
from concurrent.futures import Future
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.selfdrive.modeld.jetlink.recovery import Recovery
from openpilot.selfdrive.modeld.jetlink.link import AsyncClient
from openpilot.selfdrive.modeld.jetlink import model
import test_port


class RecoveryTests(unittest.TestCase):
  def test_retry_delays_are_bounded_and_stable_session_resets_streak(self):
    r = Recovery()
    self.assertEqual([r.failed(100.) for _ in range(10)], [1., 1., 1., 5., 10., 20., 40., 60., 60., 60.])
    r.joined(200.)
    self.assertEqual(r.failed(261.), 1.)
    self.assertEqual(r.failures, 1)

  def test_proof_requires_twenty_frames(self):
    r = Recovery()
    r.joined(10.)
    for i in range(19):
      r.note(False, 10. + i * .05, 10. + i * .05)
      self.assertTrue(r.proving)
    r.note(False, 11., 11.)
    self.assertFalse(r.proving)

  def test_hold_in_proof_after_settling_rejects(self):
    r = Recovery()
    r.joined(10.)
    for _ in range(3):
      r.note(False, 10., 10.)
    with self.assertRaisesRegex(TimeoutError, 'fell behind'):
      r.note(True, 10.05, 10.)

  def running(self):
    r = Recovery()
    r.joined(1.)
    for _ in range(20):
      r.note(False, 2., 2.)
    return r

  def test_occasional_hold_survives_and_fresh_reply_clears_streak(self):
    r = self.running()
    r.note(True, 2.05, 2.)
    r.note(False, 2.1, 2.1)
    self.assertEqual(r.consecutive, 0)

  def test_five_consecutive_holds_reject_even_with_recent_source(self):
    r = self.running()
    for _ in range(4):
      r.note(True, 2., 2.)
    with self.assertRaises(TimeoutError):
      r.note(True, 2., 2.)

  def test_twenty_one_scattered_holds_in_ten_seconds_reject(self):
    r = self.running()
    for i in range(20):
      r.note(True, 2. + i * .1, 2. + i * .1)
      r.note(False, 2. + i * .1, 2. + i * .1)
    with self.assertRaises(TimeoutError):
      r.note(True, 4., 4.)

  def test_old_holds_expire(self):
    r = self.running()
    for i in range(30):
      r.note(True, 2. + i, 2. + i)
      r.note(False, 2. + i, 2. + i)

  def test_output_age_is_bounded_even_for_real_reply(self):
    for held in (False, True):
      with self.assertRaisesRegex(TimeoutError, 'too old'):
        self.running().note(held, 2.21, 2.)


class AsyncTests(unittest.TestCase):
  def make_client(self):
    wire = SimpleNamespace(spec=object(), timeout=.15, infer=Mock(), close=Mock())
    c = AsyncClient(wire)
    self.addCleanup(c.close)
    # Deterministic futures: tests never sleep or depend on scheduler speed.
    c.pool.shutdown()
    c.pool = Mock()
    self.future = Future()
    c.pool.submit.return_value = self.future
    return c

  def test_only_one_request_pending_and_inputs_owned(self):
    c = self.make_client()
    c.received = True
    a = np.ones(4)
    with patch('openpilot.selfdrive.modeld.jetlink.link.time.monotonic', return_value=10.):
      self.assertIsNone(c.infer(a, a, 1, deadline=9.))
      a[:] = 7
      self.assertIsNone(c.infer(a, a, 2, deadline=9.))
    self.assertEqual(c.pool.submit.call_count, 1)
    np.testing.assert_array_equal(c.pool.submit.call_args.args[1], np.ones(4))
    np.testing.assert_array_equal(c.pool.submit.call_args.args[2], np.ones(4))
    self.future.set_result(np.ones(3))
    with patch('openpilot.selfdrive.modeld.jetlink.link.time.monotonic', return_value=10.1):
      np.testing.assert_array_equal(c.infer(a, a, 3, deadline=10.), np.ones(3))
    self.assertEqual(c.output_at, 10.)
    self.assertIsNone(c.pending)

  def test_late_reply_drained_before_next_frame_does_not_halve_throughput(self):
    c = self.make_client()
    c.received = True
    with patch('openpilot.selfdrive.modeld.jetlink.link.time.monotonic', return_value=10.):
      self.assertIsNone(c.infer(np.ones(1), np.ones(1), 1, deadline=9.))
    self.future.set_result(np.ones(3))
    np.testing.assert_array_equal(c.take_ready(), np.ones(3))
    second = Future()
    c.pool.submit.return_value = second
    with patch('openpilot.selfdrive.modeld.jetlink.link.time.monotonic', return_value=10.05):
      self.assertIsNone(c.infer(np.ones(1), np.ones(1), 2, deadline=9.))
    self.assertEqual(c.pool.submit.call_count, 2)

  def test_transport_timeout_is_propagated_not_held(self):
    c = self.make_client()
    self.future.set_exception(TimeoutError('wire timeout'))
    with self.assertRaisesRegex(TimeoutError, 'wire timeout'):
      c.infer(np.ones(1), np.ones(1), 1)

  def test_pending_request_hard_deadline(self):
    c = self.make_client()
    c.received = True
    with patch('openpilot.selfdrive.modeld.jetlink.link.time.monotonic', return_value=10.):
      self.assertIsNone(c.infer(np.ones(1), np.ones(1), 1, deadline=9.))
    with patch('openpilot.selfdrive.modeld.jetlink.link.time.monotonic', return_value=10.16):
      with self.assertRaisesRegex(TimeoutError, 'deadline'):
        c.infer(np.ones(1), np.ones(1), 2, deadline=9.)


class ModelRecoveryTests(unittest.TestCase):
  setUp = test_port.ModelTransitionTests.setUp

  def remote(self):
    self.adapter.warp = Mock(return_value=np.zeros(test_port.DEFAULT_SPEC.warped_shape, np.uint8))
    self.adapter.client = Mock()
    self.adapter.client.infer.return_value = np.zeros(test_port.DEFAULT_SPEC.output_nelem, np.float32)
    self.adapter.join_allowed = True
    return self.adapter

  def test_held_frame_keeps_remote_without_local_run(self):
    a = self.remote()
    a.run({}, {}, self.inputs)
    a.client.infer.return_value = None
    out = a.run({}, {}, self.inputs)
    self.assertIn('plan', out)
    self.assertTrue(a.active)
    self.local.run.assert_not_called()

  def test_proving_control_enable_forces_same_frame_native_recovery(self):
    a = self.remote()
    a.recovery.joined(1.)
    a.join_allowed = False
    self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    self.assertTrue(self.fault.exists())
    self.assertFalse(a.active)

  def test_proof_status_and_no_entry_until_twenty_frames(self):
    a = self.remote()
    a.recovery.joined(1.)
    a._write_proving()
    self.assertTrue(json.loads(self.status.read_text())['proving'])
    for _ in range(20):
      a.run({}, {}, self.inputs)
    self.assertFalse(json.loads(self.status.read_text())['proving'])

  def test_drop_ratio_demotes_to_native_and_retains_fault(self):
    a = self.remote()
    a.active = True
    a.frame_drop_ratio = .008
    self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    self.assertTrue(self.fault.exists())

  def test_moving_disengaged_can_rejoin_prepared_warp(self):
    a = self.remote()
    a.client = None
    a.ready = True
    a.small_runs = 3
    future = Future()
    client = Mock()
    client.spec = test_port.DEFAULT_SPEC
    client.infer.return_value = np.zeros(test_port.DEFAULT_SPEC.output_nelem, np.float32)
    future.set_result(client)
    with patch.object(model, 'ClientConnection', return_value=SimpleNamespace(future=future)):
      self.assertIn('plan', a.run({}, {}, self.inputs))
    self.assertTrue(a.recovery.proving)

  def test_warp_first_preparation_never_runs_while_moving(self):
    a = self.adapter
    a.ready = a.join_allowed = True
    a.prepare_allowed = False
    a.small_runs = 3
    with patch.object(model, 'WarpPreparation') as prep:
      self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
      prep.assert_not_called()


if __name__ == '__main__':
  unittest.main()
