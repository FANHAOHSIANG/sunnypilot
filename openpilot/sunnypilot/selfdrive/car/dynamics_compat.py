"""Mass-normalized bicycle-model coefficients across HKG/0.11.3 schemas."""
import math


def normalized_dynamics(CP):
  fields = CP.schema.fields
  modern = ('rotationalInertiaPerMass', 'tireStiffnessFrontPerMass', 'tireStiffnessRearPerMass')
  legacy = ('rotationalInertia', 'tireStiffnessFront', 'tireStiffnessRear')
  if all(name in fields for name in modern):
    values = tuple(float(getattr(CP, name)) for name in modern)
  elif all(name in fields for name in (*legacy, 'mass')):
    mass = float(CP.mass)
    if not math.isfinite(mass) or mass <= 0:
      raise ValueError('Cannot normalize HKG vehicle dynamics: invalid vehicle mass')
    values = tuple(float(getattr(CP, name)) / mass for name in legacy)
  else:
    raise ValueError('Unsupported CarParams dynamics schema')
  if not all(math.isfinite(value) and value > 0 for value in values):
    raise ValueError('Invalid mass-normalized vehicle dynamics')
  return values
