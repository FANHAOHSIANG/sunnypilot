"""Carrot radar identities, adapted without ECU/control dependencies.
Source: ajouatom/openpilot a564ce1dc082909b1fdc73b72e64d920336b34d6
opendbc_repo/opendbc/car/hyundai/{radar_interface,radar_group3}.py
See HUD_RADAR_LICENSE for the upstream MIT notice.
"""
CORNER_OBJECT_STABLE_TRACK_ID_START = 1000
CORNER_OBJECT_IDENTITY_STALE_CYCLES = 3
CORNER_OBJECT_IDENTITY_MAX_DREL_DELTA = 7.0
CORNER_OBJECT_IDENTITY_MAX_YREL_DELTA = 3.2
CORNER_OBJECT_HANDOFF_MAX_DREL_DELTA = 2.0
CORNER_OBJECT_HANDOFF_MAX_YREL_DELTA = 1.0
CORNER_OBJECT_HANDOFF_MAX_VREL_DELTA = 3.0
CORNER_SIDE_OBJECT_MAX_DREL = 0.2
CORNER_SIDE_OBJECT_MIN_ABS_YREL = 1.4
CORNER_SIDE_OBJECT_MAX_ABS_YREL = 4.5

class CornerObjectTrackIdManager:
  def __init__(self):
    self.next_track_id = CORNER_OBJECT_STABLE_TRACK_ID_START
    self.source_cycles: dict[str, int] = {}
    self.track_states: dict[tuple[str, int], tuple[int, int, int, float, float, int]] = {}

  def clear_source(self, source: str):
    self.track_states = {key: value for key, value in self.track_states.items() if key[0] != source}
    self.source_cycles.pop(source, None)

  def get_track_ids(self, source: str, candidates) -> dict[int, int]:
    cycle = self.source_cycles.get(source, 0) + 1
    self.source_cycles[source] = cycle
    previous = {
      track_id: state for (state_source, track_id), state in self.track_states.items()
      if state_source == source and cycle - state[5] <= CORNER_OBJECT_IDENTITY_STALE_CYCLES
    }
    used_track_ids = set()
    assignments = {}

    # Prefer the previous CAN slot, then permit a physically continuous slot
    # handoff. Object IDs are not globally unique: two distant objects can use
    # the same ID at the same time.
    for candidate in candidates:
      slot_id, object_id, age, _, d_rel, y_rel, *_ = candidate
      matches = []
      for track_id, state in previous.items():
        previous_slot, previous_object_id, previous_age, previous_d_rel, previous_y_rel, _ = state
        if track_id in used_track_ids or object_id != previous_object_id or age < previous_age:
          continue
        d_delta = abs(d_rel - previous_d_rel)
        y_delta = abs(y_rel - previous_y_rel)
        if d_delta > CORNER_OBJECT_IDENTITY_MAX_DREL_DELTA or y_delta > CORNER_OBJECT_IDENTITY_MAX_YREL_DELTA:
          continue
        matches.append((previous_slot != slot_id, d_delta + y_delta * 1.5, track_id))

      if matches:
        track_id = min(matches)[2]
      else:
        track_id = self.next_track_id
        self.next_track_id += 1
      assignments[slot_id] = track_id
      used_track_ids.add(track_id)
      self.track_states[(source, track_id)] = (slot_id, object_id, age, d_rel, y_rel, cycle)

    self.track_states = {
      key: state for key, state in self.track_states.items()
      if key[0] != source or cycle - state[5] <= CORNER_OBJECT_IDENTITY_STALE_CYCLES
    }
    return assignments


def deduplicate_corner_candidates(candidates):
  objects = []
  for candidate in candidates:
    _, object_id, age, quality, d_rel, y_rel, v_rel, *_ = candidate
    duplicate_index = None
    for index, previous in enumerate(objects):
      if object_id != previous[1]:
        continue
      if (abs(d_rel - previous[4]) <= CORNER_OBJECT_HANDOFF_MAX_DREL_DELTA and
          abs(y_rel - previous[5]) <= CORNER_OBJECT_HANDOFF_MAX_YREL_DELTA and
          abs(v_rel - previous[6]) <= CORNER_OBJECT_HANDOFF_MAX_VREL_DELTA):
        duplicate_index = index
        break
    if duplicate_index is None:
      objects.append(candidate)
    elif (age, quality) > (objects[duplicate_index][2], objects[duplicate_index][3]):
      objects[duplicate_index] = candidate
  return objects


def corner_object_position_valid(d_rel: float, y_rel: float) -> bool:
  normal_object = 0.2 < d_rel < 180.0
  clipped_side_object = (
    0.0 <= d_rel <= CORNER_SIDE_OBJECT_MAX_DREL and
    CORNER_SIDE_OBJECT_MIN_ABS_YREL <= abs(y_rel) <= CORNER_SIDE_OBJECT_MAX_ABS_YREL
  )
  return (normal_object or clipped_side_object) and abs(y_rel) < 40.0



"""Group 3 objects move between CAN addresses; addresses are transport slots."""
from collections import Counter
from dataclasses import dataclass
import math

GROUP3_START = 0x400
GROUP3_COUNT = 30
GROUP3_TRACK_ID_START = 1_000_000


@dataclass(frozen=True)
class Group3Object:
  object_id: int
  x: float
  y: float
  v: float
  length: float

  @property
  def d_rel(self):
    return max(0.0, self.x - self.length * 0.5 - 0.1)

  @classmethod
  def from_signals(cls, msg):
    return cls(int(msg["OBJECT_ID"]), msg["LONG_DIST"], msg["LAT_DIST"], msg["REL_SPEED"], msg["OBJECT_LENGTH"])


def decode_group3(raw: bytes) -> Group3Object:
  if len(raw) != 24:
    raise ValueError("Group 3 objects require 24 bytes")
  word = int.from_bytes(raw, "little")

  def signed(start, size):
    value = (word >> start) & ((1 << size) - 1)
    return value - (1 << size) if value & (1 << (size - 1)) else value
  return Group3Object((word >> 24) & 0x7f, ((word >> 64) & 0x7ff) * 0.1,
                      signed(75, 12) * 0.05, signed(87, 11) * 0.1, ((word >> 41) & 0x7f) * 0.1)


class Group3TrackIds:
  def __init__(self):
    self.next_id = GROUP3_TRACK_ID_START
    self.previous = {}

  def update(self, objects: dict[int, Group3Object]):
    """Consume only fresh slots from one 20 Hz cycle. Return slot -> track ID."""
    counts = Counter(obj.object_id for obj in objects.values())
    assignments, current = {}, {}
    for slot, obj in sorted(objects.items()):
      # Zero is empty. An ambiguous duplicate must never share filter history.
      if not (0 < obj.object_id < 128 and counts[obj.object_id] == 1 and 0 <= obj.x < 204.7):
        continue
      if not all(math.isfinite(value) for value in (obj.x, obj.y, obj.v, obj.length)):
        continue
      previous = self.previous.get(obj.object_id)
      continuous = previous is not None and (
        abs(obj.x - (previous[1].x + previous[1].v * 0.05)) <= 8.0
        and abs(obj.y - previous[1].y) <= 3.0
        and abs(obj.v - previous[1].v) <= 4.0
      )
      # Native IDs can be recycled; also break history at the undecoded range wrap.
      if continuous:
        track_id = previous[0]
      else:
        track_id = self.next_id
        self.next_id += 1
      assignments[slot] = track_id
      current[obj.object_id] = (track_id, obj)
    self.previous = current
    return assignments


