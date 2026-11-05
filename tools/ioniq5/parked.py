"""Passively inspect the already running Carrot installation. Prints only allowlisted fields."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
import time

N16 = '01effc679eb46d6036ce0302673a87fe2dcfcc95a679daeeb929679e53c3647a'


def parked(cs):
  return str(cs.gearShifter) == 'park' and cs.standstill and math.isfinite(cs.vEgo) and abs(cs.vEgo) < .1


def model_status(folder, now):
  def fresh(name):
    try:
      value = json.loads((folder / name).read_text())
      if 0 <= now - value['updated'] < 3:
        return value
    except (OSError, ValueError, KeyError, TypeError):
      pass
    return {}
  link, model = fresh('carrot-jetlink.json'), fresh('carrot-jetlink-model.json')
  peer = link.get('peer') or {}
  return dict(usb=link.get('transport') == 'usb', mac=peer.get('carrot_host') == 'mac',
              protocol=link.get('protocol', peer.get('protocol')), n16_identified=model.get('sha256') == N16,
              external_active=model.get('active') is True)


def collect(duration):
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from opendbc.car import structs

  params = Params()
  raw = params.get("CarParams")
  if not raw:
    raise ValueError("CarParams 尚未就緒。請在 P 檔 READY、Carrot 完成車型辨識後重試。")
  with structs.CarParams.from_bytes(raw) as cp:
    if cp.carFingerprint != 'HYUNDAI_IONIQ_5':
      raise ValueError("目前 CarParams 並非 HYUNDAI_IONIQ_5")
    car = dict(name=cp.carFingerprint, flags=int(cp.flags), ext_flags=int(cp.extFlags), longitudinal=cp.openpilotLongitudinalControl,
               safety=[(str(s.safetyModel), s.safetyParam) for s in cp.safetyConfigs],
               ecu_types=sorted({str(f.ecu) for f in cp.carFw}), firmware_count=len(cp.carFw))

  sm = messaging.SubMaster(['carState', 'navRoute', 'navInstruction', 'deviceState'], poll='carState')
  sm.update(1500)
  if not (sm.updated['carState'] and sm.valid['carState'] and parked(sm['carState'])):
    raise ValueError("需要即時、有效的 P 檔靜止狀態")
  sock = messaging.sub_sock('can', timeout=200)
  seen = defaultdict(set)
  samples = Counter()
  deadline = time.monotonic() + duration
  while time.monotonic() < deadline:
    sm.update(0)
    if not sm.valid['carState'] or not 0 <= time.monotonic() - sm.logMonoTime['carState'] / 1e9 < .5 or not parked(sm['carState']):
      raise ValueError("車輛離開 P 檔、移動或訊號中斷，已停止")
    if sm.updated['navRoute'] and sm.valid['navRoute'] and len(sm['navRoute'].coordinates) >= 2:
      samples['valid_route_events'] += 1
    if sm.updated['navInstruction'] and sm.valid['navInstruction']:
      samples['valid_nav_instruction_events'] += 1
    msg = messaging.recv_one(sock)
    if msg is None or not msg.valid or not 0 <= time.monotonic() - msg.logMonoTime / 1e9 < .5:
      continue
    samples['can_events'] += 1
    for f in msg.can:
      if f.src not in range(8):
        continue
      if 0 <= f.address <= 0x7ff:
        seen[f.src].add((hex(f.address), len(f.dat)))
  if not samples['can_events']:
    raise ValueError("未收到有效 CAN 封包")
  display = {}
  for key in ('ClusterHud', 'ClusterHudOrientation', 'ClusterHudScreenMode', 'EnableRadarTracks', 'EnableCornerRadar', 'CanfdHDA2'):
    try:
      display[key] = params.get_int(key)
    except (KeyError, ValueError, TypeError):
      pass
  health = {}
  if sm.valid['deviceState'] and 0 <= time.monotonic() - sm.logMonoTime['deviceState'] / 1e9 < 3.:
    ds = sm['deviceState']
    health = dict(cpu_temperatures=list(ds.cpuTempC), gpu_temperatures=list(ds.gpuTempC),
                  memory_used_percent=ds.memoryUsagePercent, storage_free_percent=ds.freeSpacePercent,
                  network_type=str(ds.networkType))
  return dict(car=car, device=health, observed_ids={k: sorted(v) for k, v in seen.items()}, samples=dict(samples),
              jetlink=model_status(Path('/dev/shm'), time.monotonic()), settings=display)


if __name__ == '__main__':
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument('--seconds', type=int, choices=range(5, 91), default=20, metavar='5..90')
  args = ap.parse_args()
  try:
    print(json.dumps(collect(args.seconds), ensure_ascii=False, indent=2))
  except (OSError, ValueError, ImportError) as exc:
    sys.exit(str(exc))
