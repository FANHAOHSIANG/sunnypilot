"""Read-only HUD radar/source inventory. No CAN transmit or ECU configuration."""
import argparse
from collections import Counter
import math
import time


def corner_candidate(address, bus, length):
  # Candidate addresses only: presence does NOT establish a valid object layout.
  return ((address in (0x162, 0x1EA) and bus in (2, 6) and length >= 24) or
          (bus == 1 and length == 32 and (0x235 <= address <= 0x248 or 0x180 <= address <= 0x184)))


def describe(points):
  usable, unknown, sides = 0, 0, set()
  for point in points:
    d, y, v = float(point.dRel), float(point.yRel), float(point.vRel)
    if not all(math.isfinite(value) for value in (d, y, v)):
      unknown += 1
      continue
    if not 0.2 < d <= 180. or abs(y) > 12.:
      continue
    usable += 1
    # A coarse source inventory, not lane classification or object confirmation.
    if y >= 1.8:
      sides.add('left')
    elif y <= -1.8:
      sides.add('right')
  return usable, unknown, sides


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--seconds', type=float, default=5.)
  args = parser.parse_args()
  if not 1 <= args.seconds <= 30:
    parser.error('--seconds must be between 1 and 30')
  from openpilot.cereal import messaging
  sm = messaging.SubMaster(['radarTracks', 'carState'])
  can = messaging.sub_sock('can', timeout=100)
  seen = Counter()
  samples = max_points = max_usable = max_unknown = 0
  sides, errors, blindspots = set(), set(), set()
  until = time.monotonic() + args.seconds
  print('唯讀檢查：不傳送 CAN、不啟用雷達、不修改車輛設定。', flush=True)
  while time.monotonic() < until:
    msg = messaging.recv_one(can)
    if msg is not None:
      for frame in msg.can:
        if corner_candidate(frame.address, frame.src, len(frame.dat)):
          seen[(frame.address, frame.src, len(frame.dat))] += 1
    sm.update(0)
    if sm.updated['radarTracks']:
      samples += 1
      radar = sm['radarTracks']
      max_points = max(max_points, len(radar.points))
      flags = {k for k in ('canError', 'radarFault', 'wrongConfig', 'radarUnavailableTemporary') if getattr(radar.errors, k, False)}
      errors.update(flags)
      if sm.valid['radarTracks'] and not flags:
        usable, unknown, current_sides = describe(radar.points)
        max_usable, max_unknown = max(max_usable, usable), max(max_unknown, unknown)
        sides.update(current_sides)
    if sm.updated['carState'] and sm.valid['carState']:
      for key in ('leftBlindspot', 'rightBlindspot'):
        if getattr(sm['carState'], key, False):
          blindspots.add(key)
  print(f'radarTracks 更新次數：{samples}')
  print(f'單次最多目標：{max_points}；有效位置／速度：{max_usable}；缺少座標或速度：{max_unknown}')
  print('曾出現的側向位置（非車道判定）：' + (', '.join(sorted(sides)) or '未觀察到'))
  print('雷達錯誤旗標：' + (', '.join(sorted(errors)) or '未觀察到'))
  print('曾亮起的盲點旗標：' + (', '.join(sorted(blindspots)) or '未觀察到'))
  print('角雷達候選報文（位址 / bus / bytes / 次數）：')
  for (address, bus, length), count in sorted(seen.items()):
    print(f'  0x{address:x} / {bus} / {length} / {count}')
  if not seen:
    print('  未觀察到；短時間無報文不代表硬體不支援。')
  print('候選報文存在不等於已確認其物件格式；請貼回以上結果。')


if __name__ == '__main__':
  main()
