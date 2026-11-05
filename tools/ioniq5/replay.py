"""Replay private Carrot rlog segments with the recording's schema; print aggregates only."""
import argparse
from collections import Counter
import json
from pathlib import Path
import mmap
import shutil
import struct
import sys
from tempfile import TemporaryFile

import capnp
import zstandard

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openpilot.hud.ioniq5.live import decode_tpms
from openpilot.hud.ioniq5.radar import PassiveRadar
from shadow import CurveShadow


def events(schema, stream):
  # capnp.read_multiple can silently ignore a partial trailing message. Check
  # each segment table explicitly so a damaged tail cannot count as a full log.
  if stream.seek(0, 2) == 0:
    raise EOFError("empty log")
  with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
    at = 0
    while at < len(data):
      if len(data) - at < 8:
        raise EOFError("short segment table")
      count = struct.unpack_from("<I", data, at)[0] + 1
      if count > 512:
        raise ValueError("invalid Cap'n Proto segment count")
      header = ((count + 2) // 2) * 8
      if at + header > len(data):
        raise EOFError("partial segment table")
      words = sum(struct.unpack_from(f"<{count}I", data, at + 4))
      end = at + header + words * 8
      if end > len(data):
        raise EOFError("partial final event")
      if words > 8 * 1024 * 1024:
        raise ValueError("oversized event")
      with schema.Event.from_bytes(data[at:end]) as event:
        yield event
      at = end


def replay(schema, path):
  counts, features, radar_peak = Counter(), Counter(), Counter()
  decoders = {n: PassiveRadar(n) for n in (0, 1, 2)}
  shadow, cp = CurveShadow(), None
  complete = True
  with TemporaryFile() as decoded:
    with path.open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as reader:
      shutil.copyfileobj(reader, decoded)
    decoded.seek(0)
    try:
      for event in events(schema, decoded):
        name, stamp = event.which(), event.logMonoTime / 1e9
        if not event.valid:
          if name in ('modelV2', 'carState', 'controlsState', 'carControl'):
            shadow.feed(name, None, stamp, False)
          continue
        counts[name] += 1
        if name == 'carParams':
          p = event.carParams
          if p.carFingerprint != 'HYUNDAI_IONIQ_5':
            raise ValueError('Expected a recorded HYUNDAI_IONIQ_5')
          cp = dict(fingerprint=p.carFingerprint, flags=p.flags, firmware_entries=len(p.carFw),
                    longitudinal=p.openpilotLongitudinalControl, tuning=p.lateralTuning.which(),
                    safety=[(str(s.safetyModel), s.safetyParam) for s in p.safetyConfigs])
        elif name == 'can':
          for frame in event.can:
            decoder = decoders.get(frame.src)
            if decoder is not None:
              decoder.feed(frame.address, frame.src, frame.dat, stamp, stamp)
              if frame.address == 0x3a0 and len(decode_tpms(frame.dat)) == 4:
                features[f'tpms_all_four_bus_{frame.src}'] += 1
        elif name in ('modelV2', 'carState', 'controlsState', 'carControl'):
          result = shadow.feed(name, getattr(event, name), stamp)
          if result:
            features['evaluated_model_frames'] += 1
            features['curve_decel_frames'] += int(result['decel'])
          if name == 'modelV2':
            for bus, decoder in decoders.items():
              snap = decoder.snapshot(stamp, '')
              radar_peak[bus] = max(radar_peak[bus], len(snap['points']))
              for group in snap['groups']:
                features[f'{group}_bus_{bus}_model_frames'] += 1
        elif name == 'navRoute' and len(event.navRoute.coordinates) >= 2:
          features['nonempty_routes'] += 1
    except EOFError:
      complete = False
  return dict(complete=complete, car=cp, valid_can=counts['can'], valid_models=counts['modelV2'],
              radar_peak=dict(radar_peak), features=dict(features))


def main():
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument('--carrot', type=Path, default=Path(__file__).with_name('carrot-reference'))
  ap.add_argument('segments', type=Path, nargs='+')
  args = ap.parse_args()
  cereal = args.carrot.resolve() / 'openpilot/cereal'
  car = args.carrot.resolve() / 'opendbc_repo/opendbc/car'
  schema = capnp.load(str(cereal / 'log.capnp'), imports=[str(cereal), str(car)])
  ok = True
  for i, segment in enumerate(args.segments, 1):
    result = replay(schema, segment)
    print(json.dumps(dict(segment=i, **result)), flush=True)
    ok &= result['complete'] and result['car'] is not None and result['valid_can'] > 0
  return 0 if ok else 2


if __name__ == '__main__':
  raise SystemExit(main())
