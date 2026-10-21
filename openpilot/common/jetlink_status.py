"""Small, dependency-free status reader shared by UI and selfdrived."""
import json
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
  return str(link.get('error') or link.get('state', 'unavailable'))[:180]


def proving_active():
  return bool(read_status(MODEL_STATUS).get('proving'))


def fallback_active(now=None, model=None):
  now = time.monotonic() if now is None else now
  model = read_status(MODEL_STATUS, now=now) if model is None else model
  until = model.get('fallback_until', 0.)
  return bool(model.get('state') == 'local' and not model.get('active') and
              isinstance(until, (int, float)) and 0 < until - now <= 5.)
