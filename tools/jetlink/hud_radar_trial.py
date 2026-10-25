#!/usr/bin/env python3
"""Opt-in, time-limited passive radar-to-HUD test for Hyundai Ioniq 5 CAN FD."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import PassiveRadar, TRIAL_PACKET, FAMILIES


from openpilot.selfdrive.modeld.jetlink.hud_radar_live import installed_bus


def hud_count(now):
  from openpilot.selfdrive.modeld.jetlink.hud import PACKET, HEADER, MAX_BYTES
  try:
    with PACKET.open('rb') as stream:
      raw = stream.read(MAX_BYTES + HEADER.size + 1)
    if not HEADER.size < len(raw) <= MAX_BYTES + HEADER.size:
      return '無新鮮封包'
    stamp, = HEADER.unpack_from(raw)
    if not 0 <= now - stamp < .5:
      return '封包已過期'
    value = json.loads(raw[HEADER.size:])
    points = value['hkg'].get('radarTracks', {}).get('points', [])
    return sum(p.get('trackId', 0) >= 10_000_000 for p in points)
  except (OSError, ValueError, KeyError, TypeError):
    return '無封包'


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--seconds', type=int, default=60)
  parser.add_argument('--publish', action='store_true', help='Send decoded targets to HUD only for this test; otherwise diagnostic only')
  args = parser.parse_args()
  if not 5 <= args.seconds <= 600:
    parser.error('--seconds must be 5..600')
  bus = installed_bus()
  from openpilot.cereal import messaging
  from openpilot.selfdrive.modeld.jetlink.hud import ENABLE
  if args.publish and not ENABLE.is_file():
    raise ValueError('外接 HUD 尚未開啟；請先開啟既有 HUD 開關')
  os.nice(15)
  boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  decoder = PassiveRadar(bus)
  sock = messaging.sub_sock('can', timeout=50)
  sm = messaging.SubMaster(['carState', 'deviceState', 'radarTracks'])
  stopped = False
  def stop(*_):
    nonlocal stopped
    stopped = True
  for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, stop)
  lock = TRIAL_PACKET.with_suffix('.lock').open('a')
  try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except BlockingIOError:
    lock.close()
    raise ValueError('已有 HUD 雷達實測程式執行中')
  tmp = TRIAL_PACKET.with_name(TRIAL_PACKET.name + f'.{os.getpid()}.tmp')
  max_points = 0
  until = time.monotonic() + args.seconds
  next_write = next_print = 0.
  print(f'只讀 CAN、只供 HUD，不傳送 CAN、不更改 ECU／Panda／巡航。A-CAN bus={bus}', flush=True)
  print(f'模式：{"HUD 實測" if args.publish else "只診斷"}；{args.seconds} 秒後自動停止，Ctrl+C 可提前停止。', flush=True)
  try:
    while not stopped and time.monotonic() < until:
      message = messaging.recv_one(sock)
      # Bound work per iteration. Old queued events are rejected by logMonoTime.
      for _ in range(256):
        if message is None:
          break
        now = time.monotonic()
        stamp = message.logMonoTime / 1e9
        if message.valid:
          for frame in message.can:
            decoder.feed(frame.address, frame.src, frame.dat, stamp, now)
        if _ < 255:
          message = messaging.recv_one_or_none(sock)
      sm.update(0)
      now = time.monotonic()
      record = decoder.snapshot(now, boot_id)
      max_points = max(max_points, len(record['points']))
      if args.publish and now >= next_write:
        tmp.write_text(json.dumps(record, separators=(',', ':'), allow_nan=False))
        os.replace(tmp, TRIAL_PACKET)
        next_write = now + .1
      if now >= next_print:
        fresh_car = sm.alive['carState'] and sm.valid['carState'] and 0 <= now - sm.recv_time['carState'] < .5
        gear = str(sm['carState'].gearShifter) if fresh_car else '未知'
        started = bool(sm['deviceState'].started) if sm.alive['deviceState'] and sm.valid['deviceState'] else '未知'
        sides = Counter('左' if p['yRel'] >= 1.8 else '右' if p['yRel'] <= -1.8 else '中' for p in record['points'])
        native = len(sm['radarTracks'].points) if sm.alive['radarTracks'] and sm.valid['radarTracks'] else '未知'
        groups = ','.join(record['groups']) or '尚無完整新鮮群組'
        print(f'檔位={gear} deviceState.started={started} 原生目標={native} | {groups} | '
              f'解碼目標={len(record["points"])} {dict(sides)} | HUD已送出={hud_count(now)}', flush=True)
        if record['ambiguous_front']:
          print('前雷達格式衝突，暫停輸出前雷達目標。', flush=True)
        next_print = now + 2.
      time.sleep(.01)
  finally:
    if args.publish:
      TRIAL_PACKET.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    lock.close()
  print(f'實測結束；最多解碼目標={max_points}。HUD 實測資料已停止。')
  print('候選報文群組 / bus / 收到次數（不代表格式已獲實車驗證）：')
  for (name, observed_bus), count in sorted(decoder.seen.items()):
    addresses = decoder.seen_addresses[(name, observed_bus)]
    print(f'  {name} / {observed_bus} / {count}；位址數={len(addresses)}/{FAMILIES[name][1]}')
  if not decoder.seen:
    print('  沒收到支援群組；不會自動啟用雷達或修改車輛設定。')


if __name__ == '__main__':
  try:
    main()
  except (OSError, ValueError) as error:
    print('實測停止：' + str(error), file=sys.stderr)
    raise SystemExit(1)
