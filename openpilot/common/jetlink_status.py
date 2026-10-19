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
  # Latched until modeld has fresh stopped/disengaged vehicle state, or a new
  # modeld process starts offroad. A five-second timer could permit reengagement
  # while the local model's recurrent context is still recovering.
  return FAULT.exists()


def status_text():
  model = read_status(MODEL_STATUS)
  link = read_status()
  if fault_active():
    return 'Fault — stop and disengage before retrying'
  if model.get('active') and link.get('state') == 'ready':
    return 'Active — Mac inference, Cinque v2'
  if model.get('error'):
    return str(model['error'])[:180]
  return str(link.get('error') or link.get('state', 'unavailable'))[:180]
