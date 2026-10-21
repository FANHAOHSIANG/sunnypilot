"""Small, dependency-free status reader shared by UI and selfdrived."""
import json
import math
from pathlib import Path
import time

STATUS = Path('/dev/shm/sunnypilot-jetlink.json')
MODEL_STATUS = Path('/dev/shm/sunnypilot-jetlink-model.json')
FAULT = Path('/dev/shm/sunnypilot-jetlink-fault')


def read_status(path=STATUS, now=None):
  now = time.monotonic() if now is None else now
  try:
    value = json.loads(path.read_text())
    if isinstance(value, dict) and 0 <= now - value['updated'] < 3:
      return value
  except (OSError, ValueError, KeyError, TypeError):
    pass
  return {'state': 'unavailable'}


def fault_active():
  # Fatal local recovery only. A healthy remote-to-local handover does not
  # create this latch. Existing controls-off/native-warmup clearing remains.
  return FAULT.exists()


def status_text():
  model = read_status(MODEL_STATUS)
  link = read_status()
  if fault_active():
    return 'Fault — local model recovery failed'
  if fallback_active(model=model):
    return 'Jetlink lost — small model active'
  if model.get('proving'):
    return 'Checking big model — keep cruise and MADS off'
  if (model.get('active') and link.get('state') == 'ready' and
      model.get('sha256') == link.get('sha256')):
    return 'Active — ' + str(model.get('model', 'Mac inference'))
  if model.get('state') == 'local' and model.get('fallback_count', 0) > 0:
    return 'Small model active — Jetlink waiting to rejoin'
  if model.get('error'):
    return str(model['error'])[:180]
  phases = {
    'preparing': 'Preparing camera adapter before live inference',
    'prepare_failed': 'Camera adapter failed — small model active; restart to retry',
    'not_prepared': 'Camera adapter has not been prepared',
    'waiting_controls': 'Big model ready — release cruise and MADS to switch',
    'warming_local': 'Warming small model before switching',
    'retry_backoff': 'Small model active — waiting for retry',
    'connecting': 'Connecting modeld to Jetlink',
  }
  if model.get('phase') in phases:
    return phases[model['phase']]
  if link.get('state') == 'ready' and model.get('state') == 'unavailable':
    return 'Jetlink connected — live modeld status unavailable'
  return str(link.get('error') or link.get('state', 'unavailable'))[:180]


def proving_active():
  return bool(read_status(MODEL_STATUS).get('proving'))


def fallback_active(now=None, model=None):
  now = time.monotonic() if now is None else now
  model = read_status(MODEL_STATUS, now=now) if model is None else model
  until = model.get('fallback_until', 0.)
  return bool(model.get('state') == 'local' and not model.get('active') and
              isinstance(until, (int, float)) and 0 < until - now <= 5.)


class HandoverGuard:
  """At most one second for a validated swap's pose/frequency transient.

  Inspired by zoompilot SwapEvents at b1d7041. Unlike a blanket load window,
  this never excuses missing/invalid messages, stopped processes, paramsd
  failure or localizer input failure. Pre-existing pose faults are not hidden.
  """
  MODEL_SERVICES = frozenset(('modelV2', 'deviceMotion', 'vehicleParameters', 'longitudinalPlan', 'lateralDelay'))

  def __init__(self):
    self.last_swap = self.until = 0.
    self.was_healthy = False

  def update(self, sm, now=None, model=None):
    now = time.monotonic() if now is None else now
    model = read_status(MODEL_STATUS, now=now) if model is None else model
    stamp = model.get('handover_at', 0.)
    fresh = (isinstance(model.get('updated'), (int, float)) and 0 <= now - model['updated'] < 3.)
    healthy_pose = bool(sm.seen['deviceMotion'] and sm['deviceMotion'].posenetOK and sm['deviceMotion'].inputsOK
                        and sm.seen['vehicleParameters'] and sm['vehicleParameters'].valid)
    if isinstance(stamp, (int, float)) and math.isfinite(stamp) and 0 < stamp <= now and stamp > self.last_swap:
      self.last_swap = stamp
      self.until = stamp + 1. if fresh and self.was_healthy and now - stamp < 1. else 0.
    other = [s for s in sm.alive if s not in self.MODEL_SERVICES]
    safe = (sm.all_alive() and sm.all_valid() and sm.all_checks(other)
            and 0 <= now - sm.recv_time['modelV2'] < .2
            and sm['deviceMotion'].inputsOK and sm['vehicleParameters'].valid and not fault_active())
    self.was_healthy = healthy_pose and sm.all_checks()
    return bool(fresh and safe and 0 < self.until - now <= 1.)
