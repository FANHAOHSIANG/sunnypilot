"""HUD-owned passive CAN reader. No CAN transmission or cereal publication."""
import logging
import threading
import time

from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import PassiveRadar

log = logging.getLogger(__name__)


def installed_bus():
  from openpilot.common.params import Params
  from opendbc.car import structs
  from opendbc.car.hyundai.hyundaicanfd import CanBus
  from opendbc.car.hyundai.values import HyundaiFlags
  raw = Params().get('CarParams')
  if not raw:
    raise ValueError('尚未取得 CarParams；請等 C4 完成車型辨識')
  with structs.CarParams.from_bytes(raw) as cp:
    if cp.carFingerprint != 'HYUNDAI_IONIQ_5' or not cp.flags & HyundaiFlags.CANFD:
      raise ValueError('這個實測版只接受 HYUNDAI_IONIQ_5 CAN FD')
    bus = CanBus(cp).ACAN
    if bus not in (0, 1, 4, 5):
      raise ValueError('無法確認 A-CAN bus')
    return bus



class LiveRadar:
  def __init__(self):
    self.stop = threading.Event()
    self.lock = threading.Lock()
    self.decoder = None
    self.thread = threading.Thread(target=self._run, name='hud-passive-radar', daemon=True)
    self.thread.start()

  def _run(self):
    from openpilot.cereal import messaging
    while not self.stop.is_set():
      try:
        decoder = PassiveRadar(installed_bus())
        sock = messaging.sub_sock('can', timeout=50)
        try:
          with self.lock:
            self.decoder = decoder
          while not self.stop.is_set():
            message = messaging.recv_one(sock)
            if message is None:
              continue
            now = time.monotonic()
            if message.valid:
              with self.lock:
                for frame in message.can:
                  decoder.feed(frame.address, frame.src, frame.dat, message.logMonoTime / 1e9, now)
        finally:
          with self.lock:
            self.decoder = None
          del sock
      except Exception:
        log.exception('Passive HUD radar unavailable; retrying without affecting other HUD data')
        self.stop.wait(5.)

  def snapshot(self, now, boot_id):
    with self.lock:
      return self.decoder.snapshot(now, boot_id) if self.decoder is not None else None

  def close(self):
    self.stop.set()
    self.thread.join(timeout=1.)
