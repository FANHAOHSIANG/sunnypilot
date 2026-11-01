"""HUD-owned passive CAN reader. No CAN transmission or cereal publication."""
import logging
import threading
import time

from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import PassiveRadar

log = logging.getLogger(__name__)


def installed_bus(role="ACAN"):
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
    bus = getattr(CanBus(cp), role)
    if bus not in (0, 1, 4, 5):
      raise ValueError('無法確認 A-CAN bus')
    return bus



class LiveRadar:
  def __init__(self):
    self.stop = threading.Event()
    self.lock = threading.Lock()
    self.decoder = None
    self.tpms = None
    self.thread = threading.Thread(target=self._run, name='hud-passive-radar', daemon=True)
    self.thread.start()

  def _run(self):
    from openpilot.cereal import messaging
    while not self.stop.is_set():
      try:
        decoder = PassiveRadar(installed_bus())
        tpms_bus = installed_bus("ECAN")
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
                  if frame.address == 0x3a0 and frame.src == tpms_bus and len(frame.dat) == 16 and 0 <= now - message.logMonoTime / 1e9 < .25:
                    self.tpms = (message.logMonoTime / 1e9, decode_tpms(bytes(frame.dat)))
                  decoder.feed(frame.address, frame.src, frame.dat, message.logMonoTime / 1e9, now)
        finally:
          with self.lock:
            self.decoder = None
            self.tpms = None
          del sock
      except Exception:
        log.exception('Passive HUD radar unavailable; retrying without affecting other HUD data')
        self.stop.wait(5.)

  def snapshot(self, now, boot_id):
    with self.lock:
      return self.decoder.snapshot(now, boot_id) if self.decoder is not None else None

  def tpms_snapshot(self, now):
    with self.lock:
      return dict(self.tpms[1]) if self.tpms and 0 <= now - self.tpms[0] < 5. else None

  def close(self):
    self.stop.set()
    self.thread.join(timeout=1.)


def decode_tpms(raw):
  """Carrot e944a8c5 CAN FD TPMS 0x3a0, pressures in PSI.

  DBC Motorola bytes 4..7; UNIT bits 68..67. 255/0 are unavailable.
  """
  if len(raw) != 16:
    return {}
  unit = (raw[8] >> 3) & 3
  if unit not in (0, 1):
    return {}
  factor = .725 if unit else 1.
  return {name: round(value * factor, 3) for name, value in zip(('fl', 'fr', 'rl', 'rr'), raw[4:8])
          if value not in (0, 255) and 5 <= value * factor <= 60}
