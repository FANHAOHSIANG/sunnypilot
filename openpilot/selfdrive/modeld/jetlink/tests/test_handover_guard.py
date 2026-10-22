"""Bounded settling must not forgive missing data or independent faults."""
import ast
from pathlib import Path
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.common import jetlink_status as status
from openpilot.selfdrive.modeld.jetlink.cable import wait_retry


class Messages(dict):
  def __init__(self):
    super().__init__(deviceMotion=NS(posenetOK=True, inputsOK=True), vehicleParameters=NS(valid=True))
    services = ('modelV2', 'deviceMotion', 'vehicleParameters', 'carControl', 'pandaStates', 'managerState')
    self.alive = dict.fromkeys(services, True)
    self.valid = dict(self.alive)
    self.freq = dict(self.alive)
    self.seen = dict(self.alive)
    self.recv_time = dict.fromkeys(services, 10.)
  def all_alive(self, services=None):
    return all(self.alive[s] for s in (self.alive if services is None else services))
  def all_valid(self, services=None):
    return all(self.valid[s] for s in (self.valid if services is None else services))
  def all_checks(self, services=None):
    return self.all_alive(services) and self.all_valid(services) and all(
      self.freq[s] for s in (self.freq if services is None else services))


class HandoverTests(unittest.TestCase):
  def setUp(self):
    self.sm = Messages()
    self.guard = status.HandoverGuard()
    self.fault = patch.object(status, 'fault_active', return_value=False)
    self.fault.start()
    self.addCleanup(self.fault.stop)
    self.guard.update(self.sm, now=10., model={'updated': 10.})

  def check(self, now=10.1, stamp=10.):
    self.sm.recv_time['modelV2'] = now
    return self.guard.update(self.sm, now=now, model={'updated': now, 'handover_at': stamp})

  def test_pose_and_model_frequency_transients_end_at_original_deadline(self):
    self.sm['deviceMotion'].posenetOK = False
    self.sm.freq['modelV2'] = False
    self.assertTrue(self.check())
    self.assertTrue(self.check(now=10.99))
    self.assertFalse(self.check(now=11.))
    self.assertFalse(self.check(now=11.1))

  def test_existing_pose_fault_does_not_acquire_a_window(self):
    self.sm['deviceMotion'].posenetOK = False
    self.guard.update(self.sm, now=10., model={'updated': 10.})
    self.assertFalse(self.check())

  def test_missing_invalid_and_nonmodel_frequency_faults_are_never_excused(self):
    for channel in self.sm.alive:
      for table in (self.sm.alive, self.sm.valid):
        table[channel] = False
        with self.subTest(channel=channel):
          self.assertFalse(self.check())
        table[channel] = True
    self.sm.freq['carControl'] = False
    self.assertFalse(self.check())

  def test_localizer_input_params_and_fatal_native_failures_are_not_excused(self):
    self.sm['deviceMotion'].inputsOK = False
    self.assertFalse(self.check())
    self.sm['deviceMotion'].inputsOK = True
    self.sm['vehicleParameters'].valid = False
    self.assertFalse(self.check())
    self.sm['vehicleParameters'].valid = True
    with patch.object(status, 'fault_active', return_value=True):
      self.assertFalse(self.check())

  def test_stale_status_future_or_malformed_swap_and_old_model_never_settle(self):
    for stamp in (float('nan'), float('inf'), 10.2, '10', None, 0., 8.):
      with self.subTest(stamp=stamp):
        self.assertFalse(self.check(stamp=stamp))
    self.sm.recv_time['modelV2'] = 9.
    self.assertFalse(self.guard.update(self.sm, now=10.1, model={'updated': 10.1, 'handover_at': 10.}))
    self.assertFalse(self.guard.update(self.sm, now=10.1, model={'updated': 5., 'handover_at': 10.}))

  def test_replug_skips_wait_once_and_unchanged_cable_keeps_two_second_pacing(self):
    for sequence, expected in (([False, True], .25), ([True, False, True], .5), ([True] * 9, 2.)):
      now = [0.]
      def pause(delay):
        now[0] += delay
      state = iter(sequence)
      wait_retry(lambda: next(state), clock=lambda: now[0], pause=pause)
      self.assertEqual(now[0], expected)

  def test_production_localizer_block_only_buffers_pose_not_inputs_or_params(self):
    # Execute the real event block, including its conditions, without loading
    # the vehicle/UI/realtime libraries on a workstation.
    root = Path(__file__).resolve().parents[5]
    tree = ast.parse((root / 'openpilot/selfdrive/selfdrived/selfdrived.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SelfdriveD')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update_events')
    block = next(n for n in method.body if isinstance(n, ast.If) and
                 any(isinstance(k, ast.Attribute) and k.attr == 'posenetInvalid' for k in ast.walk(n)))
    code = compile(ast.Module(body=[block], type_ignores=[]), '<actual localizer event block>', 'exec')
    events = set()
    self.sm['deviceMotion'].posenetOK = self.sm['deviceMotion'].inputsOK = False
    self.sm['vehicleParameters'].valid = False
    env = dict(self=NS(sm=self.sm, CP=NS(notCar=False), events=NS(add=events.add)),
               EventName=NS(posenetInvalid='pose', locationdTemporaryError='inputs', paramsdTemporaryError='params'),
               log=NS(ExtrinsicsCalibration=NS(Status=NS(calibrated=1))), cal_status=1,
               TESTING_CLOSET=False, SIMULATION=False, REPLAY=False, big_model_settling=False, jetlink_settling=True)
    exec(code, env)
    self.assertEqual(events, {'inputs', 'params'})
    events.clear()
    env['jetlink_settling'] = False
    exec(code, env)
    self.assertEqual(events, {'pose', 'inputs', 'params'})


if __name__ == '__main__':
  unittest.main()
