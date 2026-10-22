#!/usr/bin/env python3
"""Read-only C4 speed/CommaView diagnosis. No tokens, VIN, pairing or route data."""
import json
from pathlib import Path
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ('carState', 'uiStateOnroad', 'carParams', 'controlsState', 'selfdriveState')
COUNTERS = ('mode', 'subscribed', 'receiveCount', 'emittedCount', 'sampledEmitCount',
            'lastReceiveMs', 'lastEmitMs', 'sendFailureCount', 'sendBackpressureCount')


def read_json(path):
  try:
    return json.loads(Path(path).read_text())
  except (OSError, ValueError):
    return {}


def snapshot():
  stats = read_json('/data/commaview/run/telemetry-stats.json')
  stats = stats.get('runtimeStats', stats)
  services = stats.get('services', {})
  return {name: {key: services.get(name, {}).get(key) for key in COUNTERS
                 if key in services.get(name, {})} for name in SERVICES}


def main():
  from openpilot.common.params import Params
  import cereal.messaging as messaging
  p = Params()
  flags = {}
  for key in ('IsOffroad', 'HideVEgoUI', 'TrueVEgoUI', 'IsMetric'):
    try:
      flags[key] = p.get_bool(key)
    except Exception:
      flags[key] = 'unavailable'
  print('設定:', json.dumps(flags))
  helper = ROOT / 'openpilot/selfdrive/ui/commaview_export.py'
  ui = ROOT / 'openpilot/selfdrive/ui/ui_state.py'
  print('UI 匯出掛鉤:', json.dumps({'helper_exists': helper.is_file(),
                                  'hook_present': 'commaview_export' in ui.read_text()}))
  for name in ('config/runtime-debug.json', 'run/runtime-debug-effective.json'):
    config = read_json('/data/commaview/' + name)
    services = config.get('services', config.get('effectiveConfig', {}).get('services', {}))
    print(name + ':', json.dumps({k: services.get(k, 'unavailable') for k in SERVICES}))
  try:
    with urlopen('http://127.0.0.1:5002/commaview/onroad-ui-export/status', timeout=2) as response:
      status = json.load(response)
    print('掛鉤驗證:', json.dumps({k: status[k] for k in
          ('patchVerified', 'uiReloadRequired', 'uiReloadPending', 'platform', 'flavor', 'status') if k in status}))
  except Exception as exc:
    print('掛鉤驗證不可讀:', type(exc).__name__)
  before = snapshot()
  sm = messaging.SubMaster(['carState'])
  end = time.monotonic() + 2.
  samples = []
  while time.monotonic() < end:
    sm.update(100)
    if sm.updated['carState']:
      cs = sm['carState']
      samples.append((float(cs.vEgo) * 3.6, float(cs.vEgoCluster) * 3.6))
  print('C4 原生車速:', json.dumps({'samples': len(samples), 'valid': sm.valid['carState'],
                                   'alive': sm.alive['carState'],
                                   'latest_kph': samples[-1][0] if samples else None,
                                   'cluster_kph': samples[-1][1] if samples else None}))
  after = snapshot()
  print('CommaView 遙測:', json.dumps(after))
  delta = {name: after[name]['emittedCount'] - before[name]['emittedCount']
           if 'emittedCount' in after[name] and 'emittedCount' in before[name] else None for name in SERVICES}
  print('兩秒內送出增量:', json.dumps(delta))
  stats = read_json('/data/commaview/run/telemetry-stats.json')
  socket = stats.get('runtimeStats', stats).get('uiExportSocket', {})
  print('UI 匯出連線:', json.dumps({k: socket[k] for k in
        ('running', 'connected', 'connectCount', 'acceptedCount', 'malformedCount', 'lastReceiveMs') if k in socket}))
  if flags.get('HideVEgoUI') is True:
    print('注意：隱藏時速設定為開啟；先確認這是否符合你的顯示需求。')
  if not samples or not sm.valid['carState']:
    print('目前尚無有效原生車速，不能歸因於 CommaView 顯示端。')
  elif after['carState'].get('mode') == 'off':
    print('CommaView 的 carState 遙測被設定為 off。')
  elif (delta['carState'] or 0) > 0:
    print('CommaView 有送出 carState；若仍無時速，需核對 App 接收/解析與顯示設定。')
  else:
    print('原生車速有效，但尚未證明 CommaView 正在送出 carState；查看通道與接收端連線。')
  print('請保持 CommaView 即時畫面開啟；停車時車速為 0 屬正常，無資料與 0 不相同。')


if __name__ == '__main__':
  main()
