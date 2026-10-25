"""Read-only hkg -> Carrot display adapter; no CAN or control publishers.

JSON carries a selected field subset, never cross-fork serialized Cap'n Proto.
Units and model/radar axes are preserved for the existing Carrot renderer.
"""
import base64
import json
import math
import os
import time

from openpilot.selfdrive.modeld.jetlink.hud import ENABLE, PACKET, HEADER, MAX_BYTES, PERIOD

SOURCE = 'hkg_hud_v1'
SERVICES = ('carState', 'controlsState', 'selfdriveState', 'carControl', 'modelV2', 'radarState', 'deviceState', 'radarTracks')
LINE = {'x': 17, 'y': 17, 'z': 17, 't': 17}
# Only the first prediction sample is consumed by Carrot's visual lead parser.
MODEL_LEAD = {**{k: 1 for k in ('x', 'y', 'v', 'a', 'xStd', 'yStd')}, 'prob': None, 'probTime': None}
LEAD = {k: None for k in ('status', 'dRel', 'yRel', 'vRel', 'aRel', 'vLead', 'vLeadK', 'aLeadK', 'radar', 'modelProb', 'radarTrackId')}
FIELDS = {
  'carState': {**{k: None for k in ('vEgo', 'vEgoCluster', 'aEgo', 'steeringAngleDeg', 'steeringPressed',
    'leftBlinker', 'rightBlinker', 'leftBlindspot', 'rightBlindspot', 'brakePressed', 'gasPressed',
    'standstill', 'gearShifter', 'vCruise', 'vCruiseCluster')},
    'cruiseState': {k: None for k in ('enabled', 'available', 'standstill', 'speed', 'speedCluster')}},
  'controlsState': {k: None for k in ('curvature', 'desiredCurvature')},
  'selfdriveState': {k: None for k in ('enabled', 'active', 'state', 'experimentalMode', 'personality',
    'alertText1', 'alertText2', 'alertSize', 'alertStatus', 'alertType')},
  'carControl': {k: None for k in ('latActive', 'longActive', 'enabled')},
  'modelV2': {'position': LINE, 'laneLines': [LINE, 4], 'laneLineProbs': 4, 'roadEdges': [LINE, 2],
    'leadsV3': [MODEL_LEAD, 3], 'velocity': {'x': 1},
    'roadEdgeStds': 2, 'meta': {k: None for k in ('laneChangeState', 'laneChangeDirection')},
    'frameId': None, 'frameDropPerc': None, 'modelExecutionTime': None},
  'radarState': {'leadOne': LEAD, 'leadTwo': LEAD},
  'deviceState': {'started': None, 'deviceType': None},
  'radarTracks': {},  # sanitized separately; unknown lateral position is not zero
}


def get(value, key, default=None):
  try:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
  except Exception:
    return default


def select(value, spec):
  if value is None:
    return None
  if isinstance(spec, dict):
    return {k: v for k, rule in spec.items() if (v := select(get(value, k), rule)) is not None}
  if isinstance(spec, list):
    return [select(v, spec[0]) for v in list(value)[:spec[1]]]
  if isinstance(spec, int):
    values = list(value)
    if len(values) > spec:
      values = values[:1] if spec == 1 else [values[round(i * (len(values) - 1) / (spec - 1))] for i in range(spec)]
    selected = [select(v, None) for v in values]
    return selected if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in selected) else []
  if isinstance(value, bool):
    return value
  if isinstance(value, (int, float)):
    return round(value, 4) if math.isfinite(value) else None
  return str(value)[:200]


def radar_tracks(value):
  errors = get(value, 'errors')
  if any(get(errors, key, False) for key in ('canError', 'radarFault', 'wrongConfig', 'radarUnavailableTemporary')):
    return None
  points = []
  for point in get(value, 'points', ()):
    fields = {key: get(point, key) for key in ('trackId', 'dRel', 'yRel', 'vRel')}
    # HKG SCC-only tracks have yRel=NaN. They cannot locate adjacent vehicles;
    # preserve leadOne/leadTwo separately instead of inventing a center position.
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in fields.values()):
      continue
    if not 0.2 < fields['dRel'] <= 180. or abs(fields['yRel']) > 12.:
      continue
    points.append(select(fields, dict.fromkeys(fields)))
  points.sort(key=lambda p: (p['dRel'], p['trackId']))
  return {'points': points[:32]}


def snapshot(sm, now, onroad, metric, active=False):
  data, age = {}, {}
  for name in SERVICES:
    elapsed = now - sm.recv_time[name]
    limit = 1.5 if name == 'deviceState' else .5
    if sm.valid[name] and sm.alive[name] and 0 <= elapsed < limit:
      item = radar_tracks(sm[name]) if name == 'radarTracks' else select(sm[name], FIELDS[name])
      if item is not None:
        data[name] = item
        age[name] = elapsed
  # HKG's current RadarState uses present; the pinned Carrot parser uses status.
  # An explicit false present must win over any legacy status value.
  if 'radarState' in data:
    for name in ('leadOne', 'leadTwo'):
      source = get(sm['radarState'], name)
      lead = data['radarState'].get(name)
      if lead is not None:
        lead['status'] = bool(get(source, 'present', get(source, 'status', False)))
  # Same compatibility fallback as sunnypilot's own speed HUD. kph stays kph.
  car = data.get('carState')
  if car is not None and car.get('vCruiseCluster') == 0 and 'controlsState' in data:
    fallback = get(get(sm['controlsState'], 'deprecated'), 'vCruise')
    if isinstance(fallback, (float, int)) and math.isfinite(fallback):
      car['vCruiseCluster'] = fallback
  settings = {'ClusterHud': '1', 'ClusterHudDebug': '0', 'ClusterHudScreenMode': '0',
              'ClusterHudBrightness': '75', 'IsOnroad': '1' if onroad else '0',
              'IsMetric': '1' if metric else '0', 'UsbGpuActive': '1' if active else '0'}
  # The envelope remains harmless to an unadapted Carrot helper: no foreign
  # cereal bytes are exposed, so it can only render its no-data screen.
  return {'version': 1, 'source': SOURCE, 'sent': now, 'hkg': data, 'age': age,
          'params': {k: base64.b64encode(v.encode()).decode() for k, v in settings.items()},
          'events': {}, 'valid': {}, 'alive': {}, 'mono': {}, 'received': {}, 'cameras': {},
          'external_compute_label': 'MAC' if active else ''}


def encode(record):
  data = json.dumps(record, separators=(',', ':'), allow_nan=False).encode()
  if len(data) > MAX_BYTES and 'radarTracks' in record['hkg']:
    # Extra targets must not crowd out the lanes/path or the primary lead.
    record = dict(record, hkg={k: v for k, v in record['hkg'].items() if k != 'radarTracks'})
    data = json.dumps(record, separators=(',', ':'), allow_nan=False).encode()
  if len(data) > MAX_BYTES:
    # Drop the optional geometry as one unit; never truncate a framed message.
    record = dict(record, hkg={k: v for k, v in record['hkg'].items() if k != 'modelV2'})
    data = json.dumps(record, separators=(',', ':'), allow_nan=False).encode()
  if len(data) > MAX_BYTES:
    raise ValueError('HUD snapshot exceeds its USB budget')
  return data


def main():
  # Decoding/serialization runs in a separate low-priority process.
  os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  os.nice(10)
  from pathlib import Path
  sm = params = None
  radar = None
  status = Path('/dev/shm/sunnypilot-jetlink-model.json')
  from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import apply_trial
  from openpilot.selfdrive.modeld.jetlink.hud_radar_live import LiveRadar
  boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  tmp = PACKET.with_suffix('.tmp')
  try:
    while True:
      if not ENABLE.is_file():
        # Stay available for live enable, but unsubscribe and do no serialization.
        if radar is not None:
          radar.close()
          radar = None
        sm = params = None
        tmp.unlink(missing_ok=True)
        PACKET.unlink(missing_ok=True)
        time.sleep(PERIOD)
        continue
      if sm is None:
        from openpilot.cereal import messaging
        from openpilot.common.params import Params
        sm, params = messaging.SubMaster(list(SERVICES)), Params()
        radar = LiveRadar()
      start = time.monotonic()
      sm.update(0)
      # SubMaster timestamps received messages during update(). A timestamp
      # from before update makes every newly received service look future-dated.
      now = time.monotonic()
      active = False
      try:
        state = json.loads(status.read_text())
        active = state.get('active') is True and 0 <= now - float(state['updated']) < 1
      except (OSError, ValueError, KeyError, TypeError):
        pass
      record = snapshot(sm, now, not params.get_bool('IsOffroad'), params.get_bool('IsMetric'), active)
      live = radar.snapshot(now, boot_id)
      if live is not None:
        apply_trial(record, now, boot_id, trial=live)
      tmp.write_bytes(HEADER.pack(now) + encode(record))
      os.replace(tmp, PACKET)
      time.sleep(max(0., PERIOD - (time.monotonic() - start)))
  finally:
    if radar is not None:
      radar.close()
    tmp.unlink(missing_ok=True)
    PACKET.unlink(missing_ok=True)


if __name__ == '__main__':
  main()
