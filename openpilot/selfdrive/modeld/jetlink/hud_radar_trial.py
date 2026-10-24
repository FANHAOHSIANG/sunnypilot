"""Passive Hyundai CAN FD decoding for the HUD only; never publishes cereal/CAN.

Signal definitions and validity gates: ajouatom/openpilot a564ce1dc082909b1fdc73b72e64d920336b34d6,
opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.py and
hyundai_canfd_corner_radar_{235,180}.dbc. See HUD_RADAR_LICENSE.
"""
from collections import Counter
import json
import math
from pathlib import Path

from openpilot.selfdrive.modeld.jetlink.hud_radar_identity import (
  CornerObjectTrackIdManager, deduplicate_corner_candidates, corner_object_position_valid,
  Group3TrackIds, decode_group3,
)

TRIAL_PACKET = Path('/dev/shm/sunnypilot-hud-radar-trial.json')
SOURCE = 'hkg_passive_radar_v1'
TTL = .25
# Name: start, count, exact DLC. 0x430/0x440 is deliberately not decoded:
# upstream calls it unvalidated range bins, not confirmed object positions.
FAMILIES = {'front210': (0x210, 16, 32), 'front3a5': (0x3a5, 32, 24),
            'front400': (0x400, 30, 24), 'corner235': (0x235, 20, 32),
            'corner180': (0x180, 5, 32)}


def family_for(address, length):
  return next((name for name, (start, count, size) in FAMILIES.items()
               if start <= address < start + count and length == size), None)


def bits(word, start, size, signed=False):
  value = (word >> start) & ((1 << size) - 1)
  return value - (1 << size) if signed and value & (1 << (size - 1)) else value


def front_slots(name, raw):
  """Group 1/2 positions use the same left-positive yRel as Carrot."""
  word = int.from_bytes(raw, 'little')
  for slot in range(2 if name == 'front210' else 1):
    offset = slot * 128
    # DBC Motorola 47|8 and 175|8 are entire bytes 5 and 21.
    if raw[5 + slot * 16] <= 10:
      continue
    x = bits(word, (64 if name == 'front210' else 63) + offset, 12 if name == 'front210' else 13) * .05
    y = bits(word, 76 + offset, 12, True) * .05
    v = bits(word, 88 + offset, 14, True) * .01
    yield slot, x, y, v


def corner_slots(name, address, raw):
  word = int.from_bytes(raw, 'little')
  start = FAMILIES[name][0]
  for slot in range(2 if name == 'corner180' else 1):
    offset = slot * 128
    quality = bits(word, 24 + offset, 7)
    age = bits(word, 32 + offset, 8)
    identity = bits(word, 44 + offset, 7)
    x = bits(word, 64 + offset, 13) * .05
    y = bits(word, 78 + offset, 12) * .05 - 102.4
    v = bits(word, 91 + offset, 12) * .05 - 100.
    vy = bits(word, 104 + offset, 10) * .05 - 25.
    ax = bits(word, 115 + offset, 9, True) * .05
    if 0 < quality <= 100 and corner_object_position_valid(x, y) and v > -99.:
      yield ((address - start) * 2 + slot, identity, age, quality, x, y, v, vy, ax)


def valid_point(point):
  if not isinstance(point, dict):
    return False
  if type(point.get('trackId')) is not int or not 0 <= point['trackId'] < 2**53:
    return False
  values = [point.get(key) for key in ('dRel', 'yRel', 'vRel')]
  if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
    return False
  d, y, v = values
  near_side = point.get('radarSource') in ('corner235', 'corner180') and 0 <= d <= .2 and 1.4 <= abs(y) <= 4.5
  return ((.2 < d <= 180 or near_side) and abs(y) <= 12 and abs(v) < 100)


class PassiveRadar:
  """One explicit A-CAN bus, complete cycles, fresh frames, stable identities.

Incomplete cycles cannot preserve their old objects. Three complete cycles
and three consecutive observations per target are needed before display.
"""
  def __init__(self, bus):
    self.bus = bus
    self.pending = {name: {} for name in FAMILIES}
    self.outputs = {}
    self.cycles = Counter()
    self.seen = Counter()
    self.seen_addresses = {}
    self.hits = {}
    self.corner_ids = CornerObjectTrackIdManager()
    self.group3_ids = Group3TrackIds()

  def feed(self, address, bus, raw, stamp, now):
    name = family_for(address, len(raw))
    if name is None or not 0 <= now - stamp < TTL:
      return
    self.seen[(name, bus)] += 1
    self.seen_addresses.setdefault((name, bus), set()).add(address)
    if bus != self.bus:
      return
    start, count, _ = FAMILIES[name]
    pending = self.pending[name]
    if pending and stamp - min(t for t, _ in pending.values()) >= .1:
      pending.clear()
    pending[address] = (stamp, bytes(raw))
    if address != start + count - 1:
      return
    # A complete contiguous family is necessary but is not proof of hardware
    # compatibility; this remains an opt-in display experiment.
    if len(pending) != count or max(t for t, _ in pending.values()) - min(t for t, _ in pending.values()) >= .1:
      pending.clear()
      self.outputs.pop(name, None)
      self.cycles[name] = 0
      self.hits.pop(name, None)
      return
    frames = dict(pending)
    pending.clear()
    if name in self.outputs and stamp - self.outputs[name][0] >= TTL:
      self.cycles[name] = 0
      self.hits.pop(name, None)
    self.cycles[name] += 1
    points = []
    if name == 'front400':
      objects = {addr: decode_group3(raw) for addr, (_, raw) in frames.items()}
      for addr, track_id in self.group3_ids.update(objects).items():
        obj = objects[addr]
        points.append(dict(trackId=10_000_000 + track_id, dRel=obj.d_rel, yRel=obj.y, vRel=obj.v, radarSource='frontRadar'))
    elif name.startswith('front'):
      base = 20_000_000 if name == 'front210' else 30_000_000
      for addr, (_, raw) in frames.items():
        for slot, x, y, v in front_slots(name, raw):
          points.append(dict(trackId=base + (addr - start) * 2 + slot, dRel=x, yRel=y, vRel=v, radarSource='frontRadar'))
    else:
      candidates = deduplicate_corner_candidates([p for addr, (_, raw) in frames.items() for p in corner_slots(name, addr, raw)])
      identities = self.corner_ids.get_track_ids(name, candidates)
      for slot, _, _, _, x, y, v, _, _ in candidates:
        points.append(dict(trackId=40_000_000 + identities[slot], dRel=x, yRel=y, vRel=v, radarSource=name))
    previous = self.hits.get(name, {})
    self.hits[name] = {}
    visible = []
    for point in points:
      if not valid_point(point):
        continue
      key = point['trackId']
      old_count, old = previous.get(key, (0, point))
      continuous = abs(point['dRel'] - old['dRel']) <= 8 and abs(point['yRel'] - old['yRel']) <= 3 and abs(point['vRel'] - old['vRel']) <= 4
      hits = min(3, old_count + 1) if continuous else 1
      self.hits[name][key] = hits, point
      if hits >= 3 and self.cycles[name] >= 3:
        visible.append(point)
    # Oldest measurement, not the write time, determines freshness.
    self.outputs[name] = (min(t for t, _ in frames.values()), visible)

  def snapshot(self, now, boot_id):
    active = {name: (stamp, points) for name, (stamp, points) in self.outputs.items() if 0 <= now - stamp < TTL}
    fronts = [name for name in active if name.startswith('front')]
    # Multiple complete front formats on one bus are ambiguous. Do not guess.
    if len(fronts) > 1:
      active = {name: value for name, value in active.items() if not name.startswith('front')}
    points = [dict(point, age=now - stamp) for stamp, group in active.values() for point in group]
    points.sort(key=lambda p: (p['dRel'], p['trackId']))
    return dict(version=1, source=SOURCE, boot_id=boot_id, updated=now,
                bus=self.bus, groups=sorted(active), ambiguous_front=len(fronts) > 1, points=points[:32])


def apply_trial(record, now, boot_id, path=TRIAL_PACKET):
  """Merge only into outgoing HUD JSON, never the car's radarTracks service."""
  try:
    with path.open('rb') as stream:
      raw = stream.read(16385)
    if len(raw) > 16384:
      return False
    trial = json.loads(raw)
    if (not isinstance(trial, dict) or trial.get('source') != SOURCE or trial.get('version') != 1 or
        trial.get('boot_id') != boot_id or type(trial.get('updated')) not in (int, float)):
      return False
    elapsed = now - trial['updated']
    if not 0 <= elapsed < TTL or not isinstance(trial.get('points'), list) or len(trial['points']) > 32:
      return False
    # A relative-speed display needs current ego speed; never manufacture it.
    ego = record['hkg'].get('carState', {}).get('vEgo')
    if type(ego) not in (int, float) or not math.isfinite(ego) or ego < 0:
      return False
    points, ages = [], []
    for point in trial['points']:
      if not valid_point(point) or type(point.get('age')) not in (int, float) or not 0 <= point['age'] + elapsed < TTL:
        continue
      if point.get('radarSource') not in ('frontRadar', 'corner235', 'corner180'):
        continue
      points.append(dict(trackId=point['trackId'], radarSource=point['radarSource'],
                         **{key: round(point[key], 4) for key in ('dRel', 'yRel', 'vRel')}))
      ages.append(point['age'] + elapsed)
    if not points:
      return False
    # Prefer native tracks for duplicate positions. Preserve primary radarState.
    native = record['hkg'].get('radarTracks', {}).get('points', [])
    merged = list(native)
    for point in points:
      if not any(abs(point['dRel'] - p['dRel']) < 1.5 and abs(point['yRel'] - p['yRel']) < .7 and abs(point['vRel'] - p['vRel']) < 2 for p in merged):
        merged.append(point)
    merged.sort(key=lambda p: (p['dRel'], p['trackId']))
    record['hkg']['radarTracks'] = {'points': merged[:32]}
    record['age']['radarTracks'] = max(ages + ([record['age'].get('radarTracks', 0)] if native else []))
    return True
  except (OSError, ValueError, TypeError, KeyError, OverflowError):
    return False
