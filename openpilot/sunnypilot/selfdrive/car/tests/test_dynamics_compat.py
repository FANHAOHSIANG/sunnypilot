"""Use the pinned HKG CarParams schema; no vehicle or native Kalman build."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from opendbc.car.structs import car
from openpilot.sunnypilot.selfdrive.car.dynamics_compat import normalized_dynamics

ROOT = Path(__file__).resolve().parents[5]


class DynamicsCompatibilityTests(unittest.TestCase):
  def setUp(self):
    self.cp = car.CarParams.new_message(mass=2340, rotationalInertia=3500,
                                       tireStiffnessFront=120000, tireStiffnessRear=140000,
                                       wheelbase=3, centerToFront=1.2, steerRatio=14)

  def test_pinned_hkg_has_only_legacy_coefficients(self):
    self.assertNotIn('rotationalInertiaPerMass', self.cp.schema.fields)
    np.testing.assert_allclose(normalized_dynamics(self.cp.as_reader()),
                               np.array([3500, 120000, 140000]) / 2340)

  def test_modern_schema_is_not_divided_twice(self):
    names = ('rotationalInertiaPerMass', 'tireStiffnessFrontPerMass', 'tireStiffnessRearPerMass')
    values = normalized_dynamics(self.cp)
    modern = SimpleNamespace(schema=SimpleNamespace(fields=dict.fromkeys(names)), **dict(zip(names, values)))
    self.assertEqual(normalized_dynamics(modern), values)

  def test_invalid_legacy_mass_is_rejected(self):
    for mass in (0, -1, float('nan'), float('inf')):
      with self.subTest(mass=mass):
        self.cp.mass = mass
        with self.assertRaisesRegex(ValueError, 'vehicle mass'):
          normalized_dynamics(self.cp)

  def test_invalid_coefficients_are_rejected(self):
    for value in (0, -1, float('nan'), float('inf')):
      self.cp.rotationalInertia = value
      with self.assertRaisesRegex(ValueError, 'vehicle dynamics'):
        normalized_dynamics(self.cp)

  def test_unknown_schema_is_rejected(self):
    with self.assertRaisesRegex(ValueError, 'schema'):
      normalized_dynamics(SimpleNamespace(schema=SimpleNamespace(fields={})))

  def test_actual_paramsd_constructor_accepts_pinned_hkg_reader(self):
    # Execute the actual constructor with a recording Kalman, avoiding native
    # generated-library setup. This validates the reader access and units.
    source = ROOT / 'openpilot/selfdrive/locationd/paramsd.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VehicleParamsLearner')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    module = ast.fix_missing_locations(ast.Module(body=[
      ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), init,
    ], type_ignores=[]))
    kalman = Mock()
    factory = Mock(return_value=kalman)
    factory.initial_x = np.ones((9, 1))
    factory.P_initial = np.eye(9)
    scope = dict(CarKalman=factory, GENERATED_DIR='unused', normalized_dynamics=normalized_dynamics,
                 States=SimpleNamespace(STEER_RATIO=0, STIFFNESS=1, ANGLE_OFFSET=2), PoseCalibrator=Mock())
    exec(compile(module, str(source), 'exec'), scope)
    learner = SimpleNamespace(reset=Mock())
    scope['__init__'](learner, self.cp.as_reader(), 14, 1, 0)
    actual = kalman.set_globals.call_args.kwargs
    np.testing.assert_allclose([actual['rotational_inertia'], actual['stiffness_front'], actual['stiffness_rear']],
                               np.array([3500, 120000, 140000]) / 2340)
    self.assertAlmostEqual(actual['center_to_rear'], 1.8, places=6)
    learner.reset.assert_called_once_with(None)


if __name__ == '__main__':
  unittest.main()
