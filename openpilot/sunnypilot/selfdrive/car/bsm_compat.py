"""Bridge 0.11.3 UI BSM queries to the pinned HKG vehicle database.

Upstream moved BSM availability to per-brand flags and a shared helper.
Tony's pinned database still sets CarParams.enableBsm in its car interfaces.
Use upstream's helper once the HKG database provides it; retain its existing
enableBsm semantics until then.
"""
try:
  from opendbc.sunnypilot.car.interfaces import has_bsm
except ImportError:
  def has_bsm(CP, CP_SP=None):
    return CP is not None and bool(CP.enableBsm)
