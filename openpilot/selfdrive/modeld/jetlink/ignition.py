"""Fresh-state gates for USB setup, separate from model admission."""
import time
import math

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


CONNECTION_SERVICES = ("carState", "selfdriveState", "carControl", "carControlSP")
CONNECTION_MAX_AGE = .25


def connection_allowed(params, ignition_sm, vehicle_sm, now=None):
  """Background connection may run onroad, independently of model admission.

  Require fresh real vehicle messages; never rewrite ignition or IsOffroad.
  USB IO and engine warm-up do not publish driving outputs. modeld's separate
  gate still refuses a model handover while cruise or MADS is enabled.
  """
  if ignition_off(params, ignition_sm, now=now):
    return True
  now = time.monotonic() if now is None else now
  if not ignition_sm.all_alive(list(SERVICES)) or not ignition_sm.all_valid(list(SERVICES)):
    return False
  if (not len(ignition_sm["pandaStates"]) or
      any(not 0 <= now - ignition_sm.logMonoTime[k] / 1e9 <= MAX_AGE for k in SERVICES)):
    return False
  vehicle_sm.update(0)
  if not vehicle_sm.all_alive(list(CONNECTION_SERVICES)) or not vehicle_sm.all_valid(list(CONNECTION_SERVICES)):
    return False
  if any(not 0 <= now - vehicle_sm.logMonoTime[k] / 1e9 < CONNECTION_MAX_AGE for k in CONNECTION_SERVICES):
    return False
  return bool(math.isfinite(vehicle_sm["carState"].vEgo))
