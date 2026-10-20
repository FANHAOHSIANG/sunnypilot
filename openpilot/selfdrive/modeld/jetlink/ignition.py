"""Fail closed unless fresh device and Panda messages confirm ignition off."""
import time

SERVICES = ("deviceState", "pandaStates")
MAX_AGE = 1.5

def ignition_off(params, sm, now=None):
  sm.update(0)
  if not params.get_bool("IsOffroad"):
    return False
  if not sm.all_alive(list(SERVICES)) or not sm.all_valid(list(SERVICES)):
    return False
  now = time.monotonic() if now is None else now
  if any(not 0 <= now - sm.logMonoTime[s] / 1e9 <= MAX_AGE for s in SERVICES):
    return False
  pandas = sm["pandaStates"]
  return (not sm["deviceState"].started and bool(len(pandas)) and
          not any(p.ignitionLine or p.ignitionCan for p in pandas))
