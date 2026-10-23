"""Optional, display-only Carrot HUD transport. USB writes stay with their owner.

No cereal decoding, JSON serialization or file reads on the camera thread.
The reader retains one fresh packet; an idle USB window may send it at 5 Hz.
"""
import logging
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

log = logging.getLogger('sunnypilot.jetlink.hud')
ENABLE = Path('/data/jetlink-hud.enabled')
PACKET = Path('/dev/shm/sunnypilot-jetlink-hud.packet')
HEADER = struct.Struct('<d')
MAX_BYTES = 14 * 1024
MAX_AGE = .3
PERIOD = .2
CAPABILITY = 'carrot_hud_v1'
MESSAGE = 0x4000


def can_configure(params):
  # hkg registers IsOffroad only; manager writes it as the inverse of started.
  # A missing value is False, so startup/unknown state cannot enable changes.
  try:
    return params.get_bool('IsOffroad')
  except Exception as error:
    # Params also raises UnknownKeyName/RuntimeError, not just OSError.
    # Keep a settings/backend failure inside the UI/CLI error boundary.
    raise ValueError('Unable to read HUD offroad state') from error


def set_enabled(enabled, params):
  """Shared by the parked-only CLI and display toggle; observed live by HUD workers."""
  if not can_configure(params):
    raise ValueError('請先停妥、關閉點火並保持 C4 供電，再設定 HUD。')
  if enabled:
    ENABLE.touch(mode=0o600)
  else:
    ENABLE.unlink(missing_ok=True)


def capable(peer):
  return isinstance(peer, dict) and peer.get(CAPABILITY) is True


def supported(peer):
  return capable(peer) and ENABLE.is_file()


def read_packet(path=PACKET, now=None):
  now = time.monotonic() if now is None else now
  try:
    with path.open('rb') as stream:
      data = stream.read(HEADER.size + MAX_BYTES + 1)
    if not HEADER.size < len(data) <= HEADER.size + MAX_BYTES:
      return None
    stamp, = HEADER.unpack_from(data)
    if not 0 <= now - stamp < MAX_AGE:
      return None
    return stamp, data[HEADER.size:]
  except (OSError, struct.error):
    return None


class HudSender:
  """Called only under DirectClient._io_lock, or by the idle daemon owner."""
  def __init__(self, peer, path=PACKET):
    self.latest = None
    self.last_stamp = self.next_send = 0.
    self.disabled = False
    self.stop = threading.Event()
    self.thread = None
    self.path = path
    self.sent = self.skipped = 0
    # Keep a lightweight watcher even when disabled so enabling needs no USB reset.
    if capable(peer):
      self.thread = threading.Thread(target=self._read, name='jetlink-hud-read', daemon=True)
      try:
        self.thread.start()
      except RuntimeError:
        self.thread = None
        self.disabled = True
        log.exception('HUD reader unavailable; keep inference enabled')

  def _read(self):
    try:
      os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
      os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
      pass
    while not self.stop.is_set():
      self.latest = read_packet(self.path) if ENABLE.is_file() else None
      self.stop.wait(.05)

  def send(self, client, deadline=None, after_seq=None):
    now = time.monotonic()
    item = self.latest
    if self.disabled or self.stop.is_set() or item is None or now < self.next_send:
      return False
    # A small display packet may follow the one model request just enqueued,
    # while the host computes its reply. Never add it behind a model backlog.
    pending = getattr(client, 'pending', ())
    just_enqueued = (after_seq is not None and len(pending) == 1 and pending[0].seq == after_seq)
    if (pending and not just_enqueued) or (deadline is not None and deadline - now < .003):
      self.skipped += 1
      return False
    stamp, data = item
    if stamp == self.last_stamp or not 0 <= now - stamp < MAX_AGE:
      return False
    self.next_send = now + PERIOD
    try:
      accepted = client.t.try_send(MESSAGE, client._next_seq(), (data,))
    except Exception:
      # Display failures never consume an inference reply or reset the link.
      # A real USB failure still reaches the normal inference/lease checks.
      self.disabled = True
      log.exception('HUD disabled for this session after a transport error')
      return False
    if accepted:
      self.last_stamp = stamp
      self.sent += 1
    else:
      self.skipped += 1
    return bool(accepted)

  def close(self):
    self.stop.set()
    self.latest = None
    if self.thread is not None:
      self.thread.join(timeout=.2)


class HudSession:
  """The daemon owns the publisher across the entire modeld endpoint lease."""
  def __init__(self, peer):
    self.peer = peer
    self.process = self.sender = None

  def __enter__(self):
    if capable(self.peer):
      try:
        PACKET.unlink(missing_ok=True)
        self.process = subprocess.Popen(
          [sys.executable, '-m', 'openpilot.selfdrive.modeld.jetlink.hud_publisher'],
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.sender = HudSender(self.peer)
      except Exception:
        log.exception('HUD publisher unavailable; inference remains enabled')
    return self.sender

  def __exit__(self, *exc):
    if self.sender is not None:
      self.sender.close()
    if self.process is not None:
      self.process.terminate()
      try:
        self.process.wait(timeout=1)
      except subprocess.TimeoutExpired:
        self.process.kill()
        self.process.wait(timeout=1)
    PACKET.unlink(missing_ok=True)
