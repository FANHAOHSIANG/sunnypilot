#!/usr/bin/env python3
"""On C4: the REAL tlwatchd.main() against real capnp, isolated from the running one (2026-11-03, c3track).

One process: tlwatchd.main() in a thread under OPENPILOT_PREFIX=<prefix> with its state / C3 file / log dir moved to
OUT (TLW_* env, so the live tlwatchd and HUD are not touched; no ENABLE / C3_GO there -> never presses GO), while this
thread replays a drive's rlog segments in real time on the same prefix (carState, selfdriveState, radarState,
deviceState, gpsLocationExternal - the messages tlwatchd reads) and writes OUT/c3lights.json twice a second the way
c3_relay does, from a timeline made off-line (C3's window lights as p, and the light follower's result 'trk' at that
moment of the drive, from tl-overnight work/replay_c3track.py).

  [TLW_CODE=dir] OPENPILOT_PREFIX=tlwtest PYTHONPATH=/data/openpilot /usr/local/venv/bin/python verify_tlwatch_trk.py \\
      OUT timeline.json rlog [rlog ...]
Then: OUT/out.txt (tlwatchd's prints: must hold no 'error'), OUT/states.jsonl (its HUD state every 0.25 s),
OUT/base/runs/*/approach.jsonl ('trk' / 'by' columns)."""
import json
import os
import sys
import threading
import time

OUT, TIMELINE, RLOGS = sys.argv[1], sys.argv[2], sys.argv[3:]
assert os.environ.get('OPENPILOT_PREFIX'), 'run isolated: OPENPILOT_PREFIX=...'
os.makedirs(OUT, exist_ok=True)
os.environ['TLW_BASE'] = f'{OUT}/base'
os.environ['TLW_STATE'] = f'{OUT}/tlwatch.json'
os.environ['TLW_C3_FILE'] = f'{OUT}/c3lights.json'
sys.path.insert(0, '/data/openpilot/openpilot/yolo')
if os.environ.get('TLW_CODE'):            # the tlwatchd.py to test, before it is deployed (a copy, not the repo's)
  sys.path.insert(0, os.environ['TLW_CODE'])

from openpilot.cereal import messaging
from openpilot.tools.lib.logreader import LogReader

import tlwatchd

SERVICES = ['carState', 'selfdriveState', 'radarState', 'deviceState', 'gpsLocationExternal']
tl = json.load(open(TIMELINE))           # [[ep, p, trk or null]] sorted
log = open(f'{OUT}/out.txt', 'w', buffering=1)


class Tee:
  def write(self, s):
    log.write(s)

  def flush(self):
    pass


sys.stdout = Tee()
os.makedirs(f"/dev/shm/msgq_{os.environ['OPENPILOT_PREFIX']}", exist_ok=True)   # the prefix's own msgq directory
pm = messaging.PubMaster(SERVICES)
sys.argv = ['tlwatchd.py']
th = threading.Thread(target=tlwatchd.main, daemon=True)
th.start()
time.sleep(3)
msgs, off = [], None
for r in RLOGS:
  offs = []
  for m in LogReader(r):
    w = m.which()
    if w in SERVICES:
      msgs.append(m)
    elif w == 'clocks' and m.clocks.wallTimeNanos > 1.6e18:
      offs.append(m.clocks.wallTimeNanos - m.logMonoTime)
  off = max(offs) if offs else off
msgs.sort(key=lambda m: m.logMonoTime)
print(f'verify: {len(msgs)} messages, {len(tl)} timeline rows', file=log, flush=True)
t0_log, t0 = msgs[0].logMonoTime, time.monotonic()
k, last_c3, states, n_sent = 0, 0., open(f'{OUT}/states.jsonl', 'w'), 0
last_state = 0.
for m in msgs:
  lag = t0 + (m.logMonoTime - t0_log) / 1e9 - time.monotonic()
  if lag > 0:
    time.sleep(lag)
  pm.send(m.which(), m.as_builder())
  n_sent += 1
  now = time.monotonic()
  ep = (m.logMonoTime + off) / 1e9
  if now - last_c3 >= 0.5:
    last_c3 = now
    while k + 1 < len(tl) and tl[k + 1][0] <= ep:
      k += 1
    row = tl[k] if tl and tl[k][0] <= ep and ep - tl[k][0] <= 2.0 else None
    p = row[1] if row else None
    lights = []
    if p:
      if p[1] > 0:
        lights.append({'cls': 'light_red', 'conf': p[1], 'az': 0.5, 'el': 3.0, 'h': 10})
      if p[2] > 0:
        lights.append({'cls': 'light_green', 'conf': p[2], 'az': 0.5, 'el': 3.0, 'h': 10})
    trk = dict(row[2], age=round(max(ep - row[0], 0.), 2)) if row and row[2] else None
    j = {'cal': 'ok', 'using': 'this drive', 'idle': False, 'lights': lights, 'pin': None, 'trk': trk, 't': now}
    tmp = f'{OUT}/c3lights.json.tmp'
    with open(tmp, 'w') as f:
      json.dump(j, f)
    os.replace(tmp, f'{OUT}/c3lights.json')
  if now - last_state >= 0.25:
    last_state = now
    try:
      with open(f'{OUT}/tlwatch.json') as f:
        states.write(json.dumps({'ep': round(ep, 2), **json.load(f)}) + '\n')
    except (OSError, ValueError):
      pass
time.sleep(2)
print(f'verify: sent {n_sent}, tlwatchd thread alive {th.is_alive()}', file=log, flush=True)
os._exit(0)
