"""Recovery timing, admission proof and asynchronous IPC ownership."""
from concurrent.futures import Future
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.selfdrive.modeld.jetlink.recovery import Recovery
from openpilot.selfdrive.modeld.jetlink.link import AsyncClient
from openpilot.selfdrive.modeld.jetlink import link
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
  def test_io_worker_leaves_inherited_modeld_core_using_real_upstream_helper(self):
    from jetlink.transport import priority
    with patch.object(priority.os, 'cpu_count', return_value=8), \
         patch.object(priority.os, 'sched_getaffinity', return_value={7}), \
         patch.object(priority.os, 'sched_setscheduler') as scheduler, \
         patch.object(priority.os, 'sched_setaffinity') as affinity:
      AsyncClient._background()
    scheduler.assert_called_once()
    affinity.assert_called_once_with(0, set(range(7)))

  def test_connection_thread_also_leaves_modeld_core_before_handshake(self):
    future = Future()
    with patch.object(link, 'background_thread') as background, \
         patch.object(link, 'Client') as client, patch.object(link, 'AsyncClient') as async_client:
      client.side_effect = lambda: (background.assert_called_once() or object())
      link.ClientConnection._connect(future)
    self.assertIs(future.result(), async_client.return_value)

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

  def test_reply_finishing_at_timeout_boundary_consumed_once_with_correct_age(self):
    class BoundaryReply(Future):
      def result(self, timeout=None):
        if not self.done():
          self.set_result(np.ones(3))
          raise TimeoutError('deadline boundary')
        return super().result(timeout)
    c = self.make_client()
    c.pool.submit.return_value = BoundaryReply()
    with patch.object(link.time, 'monotonic', return_value=10.):
      np.testing.assert_array_equal(c.infer(np.ones(1), np.ones(1), 1), np.ones(3))
    self.assertIsNone(c.pending)
    self.assertTrue(c.received)
    self.assertEqual(c.output_at, 10.)
    self.assertIsNone(c.take_ready())

  def test_transport_timeout_at_wait_boundary_is_still_an_error(self):
    class BoundaryError(Future):
      def result(self, timeout=None):
        if not self.done():
          self.set_exception(TimeoutError('wire failed'))
          raise TimeoutError('wait expired')
        return super().result(timeout)
    c = self.make_client()
    c.pool.submit.return_value = BoundaryError()
    with self.assertRaisesRegex(TimeoutError, 'wire failed'):
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

  def test_failed_proving_keeps_failed_frame_metrics_after_native_handback(self):
    a = self.remote()
    a.recovery.joined(1.)
    for _ in range(3):
      a.run({}, {}, self.inputs)
    a.client.infer.return_value = None
    self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    record = json.loads(self.status.read_text())
    self.assertIn('held proving frame 4/20', record['error'])
    self.assertEqual(record['last_frame']['proving_frame'], 4)
    self.assertTrue(record['last_frame']['held'])
    for key in ('warp_pack_ms', 'ipc_wait_ms', 'total_ms', 'output_age_ms'):
      self.assertGreaterEqual(record['last_frame'][key], 0.)
    self.assertEqual(record['fallback_count'], 1)
    self.assertFalse(self.fault.exists())

  def test_drained_previous_reply_does_not_excuse_a_new_proving_frame_hold(self):
    a = self.remote()
    c = AsyncClient(SimpleNamespace(spec=test_port.DEFAULT_SPEC, timeout=.15, infer=Mock(), close=Mock()))
    self.addCleanup(c.close)
    c.pool.shutdown()
    c.pool = Mock()
    c.pending = Future()
    c.pending.set_result(np.zeros(test_port.DEFAULT_SPEC.output_nelem, np.float32))
    c.started = 9.99
    next_pending = Mock()
    next_pending.result.side_effect = TimeoutError('still working')
    next_pending.done.return_value = False
    c.pool.submit.return_value = next_pending
    a.client = c
    a.recovery.joined(9.)
    a.recovery.frames = 3
    with patch.object(link.time, 'monotonic', return_value=10.):
      self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    record = json.loads(self.status.read_text())
    self.assertIn('held proving frame 4/20', record['error'])
    self.assertTrue(record['last_frame']['drained_reply'])
    self.assertTrue(record['last_frame']['held'])

  def test_proving_control_enable_forces_same_frame_native_recovery(self):
    a = self.remote()
    a.recovery.joined(1.)
    a.join_allowed = False
    self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    self.assertFalse(self.fault.exists())
    self.assertFalse(a.active)

  def test_proof_status_and_no_entry_until_twenty_frames(self):
    a = self.remote()
    a.recovery.joined(1.)
    a._write_proving()
    self.assertTrue(json.loads(self.status.read_text())['proving'])
    for _ in range(20):
      a.run({}, {}, self.inputs)
    self.assertFalse(json.loads(self.status.read_text())['proving'])

  def test_drop_ratio_demotes_to_native_without_synthetic_fault(self):
    a = self.remote()
    a.active = True
    a.frame_drop_ratio = .008
    self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    self.assertFalse(self.fault.exists())

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

  def test_live_loop_never_prepares_a_missing_warp(self):
    a = self.adapter
    a.ready = a.join_allowed = True
    a.small_runs = 3
    with patch.object(model, 'Warp') as prep:
      self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
      prep.assert_not_called()

  def test_prepare_is_synchronous_without_remote_or_control_state(self):
    a = self.adapter
    with patch.object(model, 'Warp') as warp:
      self.assertIs(a.prepare(), a)
      self.assertIs(a.warp, warp.return_value)
      warp.assert_called_once_with(1928, 1208, input_buffers=a.warp_inputs)
    self.assertEqual(json.loads(self.status.read_text())['phase'], 'waiting_link')
    self.local.run.assert_not_called()

  def test_failed_preparation_stays_local_and_does_not_retry_in_frame_loop(self):
    a = self.adapter
    with patch.object(model, 'Warp', side_effect=RuntimeError('compiler failed')) as warp:
      a.prepare()
      a.ready = a.join_allowed = True
      for _ in range(5):
        self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
      self.assertEqual(warp.call_count, 1)
    self.assertEqual(json.loads(self.status.read_text())['phase'], 'prepare_failed')
    self.assertFalse(self.fault.exists())

  def test_successful_swap_sets_timestamp_once_not_on_each_frame(self):
    a = self.remote()
    a.run({}, {}, self.inputs)
    stamp = a.handover_at
    self.assertGreater(stamp, 0.)
    a.run({}, {}, self.inputs)
    self.assertEqual(a.handover_at, stamp)
    self.assertEqual(json.loads(self.status.read_text())['handover_at'], stamp)

  def test_preparation_precedes_realtime_and_live_loop_in_modeld(self):
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / 'modeld.py').read_text()
    self.assertLess(source.index('height).prepare()'), source.index('config_realtime_process(7, 54)'))


if __name__ == '__main__':
  unittest.main()
