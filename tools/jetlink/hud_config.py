"""Explicit parked-only opt-in for the experimental Mac display sender."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openpilot.selfdrive.modeld.jetlink.hud import ENABLE, PACKET


def main():
  parser = argparse.ArgumentParser()
  group = parser.add_mutually_exclusive_group(required=True)
  group.add_argument('--enable', action='store_true')
  group.add_argument('--disable', action='store_true')
  group.add_argument('--status', action='store_true')
  args = parser.parse_args()
  if args.status:
    print('HUD enabled:', ENABLE.is_file())
    print('Publisher packet:', PACKET.exists())
    return
  from openpilot.common.params import Params
  params = Params()
  if params.get_bool('IsOnroad') or not params.get_bool('IsOffroad'):
    raise SystemExit('請先停妥、關閉點火並保持 C4 供電，再設定 HUD。')
  if args.enable:
    ENABLE.touch(mode=0o600)
    print('已啟用測試 HUD。Mac 須先完成 hkg adapter 安裝；停車狀態重啟 C4 後生效。')
  else:
    ENABLE.unlink(missing_ok=True)
    print('已關閉 HUD；停車狀態重啟 C4 可完全退出新增的顯示程序。')


if __name__ == '__main__':
  main()
