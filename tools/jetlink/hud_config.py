"""Explicit parked-only opt-in for the experimental Mac display sender."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openpilot.selfdrive.modeld.jetlink.hud import ENABLE, PACKET, set_enabled


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
  try:
    set_enabled(args.enable, params)
  except (ValueError, OSError) as error:
    raise SystemExit(str(error)) from error
  if args.enable:
    print('已啟用測試 HUD，無須重啟。Mac 須完成 hkg adapter 安裝並連上 Jetlink。')
  else:
    print('已關閉 HUD 資料傳送，無須重啟。')


if __name__ == '__main__':
  main()
