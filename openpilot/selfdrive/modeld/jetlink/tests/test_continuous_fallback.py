"""Validated same-frame handback and real cruise/MADS transition methods.

No GL, compiled messaging transport or vehicle is needed: execute the source
state-machine classes and read event types from production event mappings.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.common import jetlink_status
from openpilot.selfdrive.modeld.jetlink import model
import test_port

ROOT = Path(__file__).resolve().parents[5]


def event_types(path, mapping, name):
  tree = ast.parse((ROOT / path).read_text())
  table = next(n.value for n in tree.body if
               (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == mapping for t in n.targets)) or
               (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == mapping))
  entry = next(v for k, v in zip(table.keys, table.values) if isinstance(k, ast.Attribute) and k.attr == name)
  return {k.attr for k in entry.keys}


def class_from_source(path, name, env):
  tree = ast.parse((ROOT / path).read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
  exec(compile(ast.Module(body=[cls], type_ignores=[]), path, 'exec'), env)
  return env[name]


class Events:
  def __init__(self, *types):
    self.types = set(types)
  def contains(self, kind):
    return kind in self.types
  def has(self, event):
    return False
  def contains_in_list(self, events):
    return False


class ContinuousFallbackTests(unittest.TestCase):
  setUp = test_port.ModelTransitionTests.setUp

  def failing_remote(self):
    a = self.adapter
    a.warp = Mock(return_value=np.zeros(test_port.DEFAULT_SPEC.warped_shape, np.uint8))
    a.client = Mock()
    a.client.infer.side_effect = TimeoutError('remote disconnected')
    a.active = True
    a.join_allowed = False
    return a

  def test_healthy_native_result_is_returned_before_any_recovery_is_announced(self):
    a = self.failing_remote()
    self.local.run.side_effect = lambda *args: (self.assertFalse(self.status.exists()) or {'local': True})
    self.assertEqual(a.run({}, {}, self.inputs), {'local': True})
    self.assertFalse(self.fault.exists())
    status = json.loads(self.status.read_text())
    self.assertEqual(status['state'], 'local')
    self.assertFalse(status['active'])
    self.assertFalse(status['proving'])
    self.assertEqual(status['fallback_count'], 1)
    self.assertGreater(status['fallback_until'], status['updated'])

  def test_invalid_empty_and_nonfinite_native_outputs_are_fatal(self):
    for output in (None, {}, {'plan': np.array([np.nan])}, {'plan': np.array([np.inf])}):
      a = self.failing_remote()
      self.local.run.return_value = output
      with self.subTest(output=output), self.assertRaises((RuntimeError, ValueError)):
        a.run({}, {}, self.inputs)
      self.assertTrue(self.fault.exists())
      self.assertEqual(a.fallback_until, 0.)

  def test_reset_failure_is_fatal_not_a_successful_handback(self):
    a = self.failing_remote()
    a._reset_small = Mock(side_effect=RuntimeError('GPU reset failed'))
    with self.assertRaisesRegex(RuntimeError, 'GPU reset failed'):
      a.run({}, {}, self.inputs)
    self.assertTrue(self.fault.exists())
    self.assertEqual(a.fallback_count, 0)
    self.local.run.assert_not_called()

  def test_following_native_failure_is_not_hidden_after_successful_handback(self):
    a = self.failing_remote()
    a.run({}, {}, self.inputs)
    self.local.run.side_effect = RuntimeError('local GPU lost')
    with self.assertRaisesRegex(RuntimeError, 'local GPU lost'):
      a.run({}, {}, self.inputs)
    self.assertTrue(self.fault.exists())
    self.assertEqual(a.fallback_until, 0.)

  def test_no_synthetic_fault_is_visible_during_successful_native_inference(self):
    a = self.failing_remote()
    def native(*args):
      self.assertFalse(self.fault.exists())
      return {'local': True}
    self.local.run.side_effect = native
    a.run({}, {}, self.inputs)

  def test_v3_handback_uses_small_model_action_delay_without_mutating_original(self):
    a = self.failing_remote()
    a.spec = model.V3_SPEC
    original = self.inputs['action_t'].copy()
    a.run({}, {}, self.inputs)
    native_inputs = self.local.run.call_args.args[2]
    np.testing.assert_allclose(native_inputs['action_t'], original - [.1, 0.])
    np.testing.assert_array_equal(self.inputs['action_t'], original)

  def test_fallback_warning_is_fresh_bounded_and_only_for_local_state(self):
    with patch.object(jetlink_status, 'MODEL_STATUS', self.status):
      self.status.write_text(json.dumps(dict(updated=100., state='local', active=False, fallback_until=105.)))
      self.assertTrue(jetlink_status.fallback_active(now=101.))
      self.assertFalse(jetlink_status.fallback_active(now=104.))  # stale modeld record
      self.assertFalse(jetlink_status.fallback_active(now=106.))
      self.assertFalse(jetlink_status.fallback_active(now=99.))
    self.assertFalse(jetlink_status.fallback_active(now=101., model=dict(state='active', active=True, fallback_until=105.)))


class ControlContinuityTests(unittest.TestCase):
  def setUp(self):
    self.State = NS(disabled='disabled', enabled='enabled', preEnabled='preEnabled',
                    softDisabling='softDisabling', overriding='overriding', paused='paused')
    names = ('PERMANENT', 'WARNING', 'IMMEDIATE_DISABLE', 'USER_DISABLE', 'SOFT_DISABLE',
             'NO_ENTRY', 'ENABLE', 'PRE_ENABLE', 'OVERRIDE_LATERAL', 'OVERRIDE_LONGITUDINAL')
    self.ET = NS(**{k:k for k in names})
    env = dict(State=self.State, ET=self.ET, Events=Events, DT_CTRL=.01, SOFT_DISABLE_TIME=3,
               ACTIVE_STATES=('enabled','softDisabling','overriding'),
               ENABLED_STATES=('enabled','softDisabling','overriding','preEnabled'))
    cruise = class_from_source('openpilot/selfdrive/selfdrived/state.py', 'StateMachine', env)
    self.cruise = cruise()
    self.cruise.state = 'enabled'
    env_mads = dict(env, GEARS_ALLOW_PAUSED=[], GEARS_ALLOW_PAUSED_SILENT=[],
                    EventNameSP=NS(silentLkasDisable='silentLkasDisable'),
                    ENABLED_STATES=('enabled','softDisabling','overriding','paused'))
    mads = class_from_source('openpilot/sunnypilot/mads/state.py', 'StateMachine', env_mads)
    self.events = Events()
    self.sp = Events(*event_types('openpilot/sunnypilot/selfdrive/selfdrived/events.py', 'EVENTS_SP', 'jetlinkFallback'))
    self.drive = NS(state_machine=self.cruise, events=self.events, events_sp=self.sp, enabled=True)
    self.mads = mads(NS(selfdrive=self.drive))
    self.mads.state = 'enabled'

  def test_fallback_alert_has_no_disable_or_no_entry_type(self):
    self.assertEqual(self.sp.types, {'PERMANENT'})

  def test_successful_handback_preserves_both_cruise_and_mads(self):
    self.assertEqual(self.cruise.update(self.events), (True, True))
    self.assertEqual(self.mads.update(), (True, True))
    self.assertEqual(self.cruise.state, 'enabled')
    self.assertEqual(self.mads.state, 'enabled')

  def test_real_faults_keep_their_original_disabling_behavior_even_with_fallback_alert(self):
    for fault in ('accFaulted', 'canError', 'commIssue', 'locationdTemporaryError', 'processNotRunning'):
      self.setUp()
      types = event_types('openpilot/selfdrive/selfdrived/events.py', 'EVENTS', fault)
      self.events.types = types
      with self.subTest(fault=fault):
        self.cruise.update(self.events)
        self.mads.update()
        self.assertNotEqual(self.cruise.state, 'enabled')
        self.assertNotEqual(self.mads.state, 'enabled')

  def test_schema_contains_serializable_non_disabling_event(self):
    import capnp
    schema = capnp.load(str(ROOT / 'openpilot/cereal/custom.capnp'), imports=[str(ROOT / 'openpilot/cereal')])
    event = schema.OnroadEventSP.Event.new_message(name='jetlinkFallback', permanent=True)
    with schema.OnroadEventSP.Event.from_bytes(event.to_bytes()) as reader:
      self.assertEqual(str(reader.name), 'jetlinkFallback')
      self.assertTrue(reader.permanent)
      self.assertFalse(reader.softDisable or reader.immediateDisable or reader.noEntry)


if __name__ == '__main__':
  unittest.main()
