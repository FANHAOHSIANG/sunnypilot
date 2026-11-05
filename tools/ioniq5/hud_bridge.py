"""Convert HCOP's read-only SSE display stream to the Mac Carrot HUD packet format."""
import argparse
import base64
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

import capnp

DONOR = Path(__file__).with_name('carrot-reference')


def schema_at(root):
  cereal = root.resolve() / 'openpilot/cereal'
  car = root.resolve() / 'opendbc_repo/opendbc/car'
  return capnp.load(str(cereal / 'log.capnp'), imports=[str(cereal), str(car)])


def packet(schema, data, nav_preview=None):
  source = data.get('ioniq5Display') or {}
  now = data.get('ts')
  if (data.get('standby') or data.get('stale') or data.get('error') or
      not isinstance(now, (float, int)) or not math.isfinite(now) or not source):
    return None
  events, mono = {}, {}

  def put(name, stamp, values):
    if type(stamp) is not int or not 0 <= now - stamp / 1e9 < .25:
      return
    event = schema.Event.new_message(logMonoTime=stamp, valid=True)
    event.init(name)
    setattr(event, name, values)
    events[name] = base64.b64encode(event.to_bytes()).decode()
    mono[name] = stamp

  state = data.get('carState', {})
  if source.get('carStateValid') and data.get('alive', {}).get('carState'):
    keys = ('vEgo', 'vEgoCluster', 'gasPressed', 'brakePressed', 'leftBlinker', 'rightBlinker',
            'standstill', 'steeringAngleDeg', 'leftBlindspot', 'rightBlindspot', 'yawRate')
    values = {k: state[k] for k in keys if k in state}
    if 'vEgo' not in values:
      return None
    values['cruiseState'] = dict(enabled=state['cruiseEnabled'], available=state['cruiseAvailable'], speed=state['cruiseSpeed'])
    values['vCruise'] = state['cruiseSpeed'] * 3.6 if state['cruiseSpeed'] > 0 else 255.
    if state.get('tpms'):
      values['tpms'] = state['tpms']
    put('carState', source.get('carStateMono'), values)
  if 'carState' not in events:
    return None
  points = [{k: p[k] for k in ('dRel', 'yRel', 'vRel', 'trackId', 'radarSource')} | {'measured': True}
            for p in source.get('points', [])[:32]]
  put('liveTracks', source.get('radarMono'), {'points': points})
  settings = {'ClusterHud': '1', 'ClusterHudScreenMode': '7', 'ClusterHudOrientation': '2',
              'IsMetric': '1', 'IsOnroad': '1'}
  record = dict(version=1, sent=now, params={k: base64.b64encode(v.encode()).decode() for k, v in settings.items()},
                events=events, received={n: v / 1e9 for n, v in mono.items()}, mono=mono,
                valid=dict.fromkeys(events, True), alive=dict.fromkeys(events, True), cameras={})
  if nav_preview:
    record['nav_map_preview'] = nav_preview
  return record


def main():
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument('--carrot', type=Path, default=DONOR)
  ap.add_argument('--output', required=True, type=Path, help='Dedicated temporary packet file for the test renderer')
  ap.add_argument('--nav-preview', type=Path, help='Optional live 256x256 local NAV preview')
  args = ap.parse_args()
  if args.output.exists():
    ap.error('choose a new, unused packet path')
  sys.path.insert(0, str(args.carrot.resolve() / 'tools/jetlink'))
  from hud_protocol import HEADER, encode_snapshot
  from hud_nav_map import NavPreviewPublisher
  schema = schema_at(args.carrot)
  preview = NavPreviewPublisher(args.nav_preview) if args.nav_preview else None
  last = None
  owned = False
  try:
    for line in sys.stdin.buffer:
      if not line.startswith(b'data: ') or len(line) > 256 * 1024:
        continue
      data = json.loads(line[6:])
      record = packet(schema, data, preview.packet(True) if preview else None)
      if record is None:
        last = None
        if owned:
          args.output.unlink(missing_ok=True)
        continue
      stamp = record['sent']
      previous, last = last, stamp
      if previous is None or not 0 < stamp - previous < .5:
        continue
      payload = HEADER.pack(time.monotonic()) + encode_snapshot(record)
      with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as tmp:
        tmp.write(payload)
      try:
        if not owned:
          os.link(tmp.name, args.output)
          owned = True
        else:
          os.replace(tmp.name, args.output)
      finally:
        Path(tmp.name).unlink(missing_ok=True)
  finally:
    if owned:
      args.output.unlink(missing_ok=True)


if __name__ == '__main__':
  main()
