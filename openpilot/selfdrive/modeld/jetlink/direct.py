"""Mac data endpoints belong to modeld; jetlinkd retains ep0 and a lease.

The Unix socket carries only ownership commands, never camera/model tensors.
Endpoint cleanup runs off the camera thread. An unacknowledged cleanup keeps
the controller unbound: a new borrower must never race a stranded USB read.
"""
import json
import logging
from pathlib import Path
import socket
import struct
import threading
import time

import numpy as np

from openpilot.selfdrive.modeld.jetlink.link import SOCKET, receive, send
from openpilot.selfdrive.modeld.jetlink.contracts import validate_model_spec
from openpilot.selfdrive.modeld.jetlink.pipeline import PipelineClient as JetlinkClient
from jetlink.spec import ModelSpec
from jetlink.transport.ffs import FfsTransport
from jetlink.transport.priority import background_thread
from openpilot.selfdrive.modeld.jetlink.hud import HudSender

log = logging.getLogger('sunnypilot.jetlink.direct')
LEASE_TIMEOUT = 5.


def packet(sock, value):
  send(sock, json.dumps(value).encode())


def read_packet(sock):
  value = json.loads(receive(sock))
  if not isinstance(value, dict):
    raise ValueError('Invalid USB lease message')
  return value


def process_identity(pid):
  """Linux start tick + state; a reused PID must not own an old lease."""
  try:
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return fields[19], fields[0]  # fields 22 (starttime) and 3 (state)
  except FileNotFoundError:
    return None


def wait_peer_exit(pid, identity, timeout=2.):
  if identity is None:
    return False
  until = time.monotonic() + timeout
  while True:
    try:
      current = process_identity(pid)
    except (OSError, ValueError, IndexError):
      return False  # An unreadable identity is not evidence of closed FDs.
    if current is None or current[0] != identity[0] or current[1] in ('Z', 'X'):
      return True
    if time.monotonic() >= until:
      return False
    time.sleep(.02)


def serve_lease(connection, owner, spec, publish):
  """One authenticated (0600 local socket) borrower until clean release."""
  clean = False
  dropped = False
  granted = False
  pid, _, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
  identity = process_identity(pid)
  connection.settimeout(LEASE_TIMEOUT)
  try:
    if read_packet(connection) != {'op': 'borrow', 'version': 1}:
      raise ValueError('Invalid USB lease request')
    if owner.session is not None or not owner.owner.lendable:
      raise RuntimeError('USB endpoints were not released before lending')
    packet(connection, dict(version=1, mount=owner.owner.mount, udc=owner.udc,
                            gadget=owner.owner.gadget, spec=spec.to_dict()))
    # A borrower opens endpoints only after receiving this complete packet.
    # Cancellation while the idle transport is closing can leave a stale
    # request here: a failed/partial grant cannot be decoded by that client
    # and must not be treated as an outstanding endpoint lease.
    granted = True
    connection.settimeout(.5)
    while True:
      publish('ready', spec=spec, transport='usb-direct', leased=True)
      try:
        request = read_packet(connection)
      except TimeoutError:
        continue
      op = request.get('op')
      if op == 'drop':
        owner.drop()
        dropped = True
      elif op == 'bounce' and not dropped:
        owner.bounce()
      elif op == 'release' and dropped and request.get('clean') is True:
        # The borrower has closed its endpoints and joined its reader before
        # this acknowledgement. Only now may the owner put USB back online.
        owner.restore()
        clean = True
      else:
        raise ValueError('Invalid USB lease transition')
      packet(connection, {'ok': True})
      if clean:
        return
  finally:
    if granted and not clean:
      # Manager stops modeld when ignition goes off. EOF alone is not enough:
      # wait for that process to exit (or become a zombie, with FDs released)
      # before rebinding. A live/D-state process cannot safely lend endpoints.
      try:
        owner.drop()
        if wait_peer_exit(pid, identity):
          owner.restore()
        else:
          owner.failed = 'USB borrower left without confirming endpoint cleanup; reboot required'
      except Exception as exc:
        owner.failed = 'USB lease cleanup failed; reboot required: ' + str(exc)
        raise


def serve_mac(listener, client, transport, owner, publish, attached, hud=None):
  """Idle readiness belongs to jetlinkd; live inference belongs to modeld."""
  spec = validate_model_spec(client.spec)
  ping_at = 0.
  while attached():
    publish('ready', spec=spec, timings=list(client.last_timings), transport='usb-direct', leased=False)
    try:
      connection, _ = listener.accept()
    except TimeoutError:
      if time.monotonic() >= ping_at:
        from openpilot.selfdrive.modeld.jetlink.daemon import check_loaded
        check_loaded(client, spec)
        ping_at = time.monotonic() + 2.
      if hud is not None:
        hud.send(client)
      continue
    with connection:
      # Finish the daemon's reader and AIO before granting modeld endpoints.
      # ep0/configfs stay resident; the Mac sees one re-enumeration at handoff.
      transport.close()
      serve_lease(connection, owner, spec, publish)
    return


class DirectClient:
  """Exclusive USB data endpoints; AIO send and bounded reply waits in modeld."""
  pipeline = True
  def __init__(self, path=SOCKET, timeout=.15):
    self.timeout = timeout
    self.timings = (0, 0, 0)
    self.metrics = {}
    self.output_at = 0.
    self.sent = False
    self._verified_frame = None
    self.transport = None
    self.hud = None
    self._io_lock = threading.Lock()
    self._rpc_lock = threading.Lock()
    self._closed = threading.Event()
    self.cleaned = threading.Event()
    self.control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    self.control.settimeout(LEASE_TIMEOUT)
    try:
      self.control.connect(path)
      packet(self.control, {'op': 'borrow', 'version': 1})
      grant = read_packet(self.control)
      if grant.get('version') != 1:
        raise ValueError('Invalid USB lease version')
      self.spec = validate_model_spec(ModelSpec.from_dict(grant['spec']))
      self.transport = FfsTransport.borrowed(grant['mount'], grant['udc'],
                                             bounce=self._bounce, owner_gadget=grant['gadget'])
      self.transport.protocol_version = 3
      self.client = JetlinkClient(self.transport, name='sunnypilot-jetlink-direct', deadline=timeout)
      from openpilot.selfdrive.modeld.jetlink.daemon import prepare_selected, read_port, UDC_ROOT
      peer = self.client.hello()
      if read_port(UDC_ROOT / grant['udc'] / 'current_speed') not in ('super-speed', 'super-speed-plus'):
        raise ValueError('Direct USB handoff requires SuperSpeed')
      if peer.get('loaded') != self.spec.sha256:
        raise ValueError('App model changed during USB handoff')
      selected = prepare_selected(self.client, peer, lambda: not self._closed.is_set())
      if selected.to_dict() != self.spec.to_dict():
        raise ValueError('Model contract changed during USB handoff')
      self.hud = HudSender(peer)
    except BaseException:
      self.close()
      raise

  def _rpc(self, op, **values):
    with self._rpc_lock:
      packet(self.control, dict(op=op, **values))
      if read_packet(self.control) != {'ok': True}:
        raise RuntimeError('USB owner did not acknowledge ' + op)

  def _bounce(self):
    self._rpc('bounce')
    return True

  def infer(self, image, packed, frame, reset=False, source_sof=0):
    if image.dtype != np.uint8 or image.shape != self.spec.warped_shape or not image.flags.c_contiguous:
      raise ValueError('invalid warped image')
    if packed.dtype != np.float32 or packed.size != self.spec.packed_nelem or not packed.flags.c_contiguous or not np.all(np.isfinite(packed)):
      raise ValueError('invalid recurrent input')
    from openpilot.selfdrive.modeld.jetlink.daemon import infer_selected, transport_stat
    with self._io_lock:
      if self._closed.is_set():
        raise ConnectionError('USB lease closing')
      started = time.monotonic()
      try:
        output = infer_selected(self.client, self.spec, image, packed, frame, reset)
        self.timings = self.client.last_timings
        return output
      finally:
        self.metrics = dict(frame=frame, exchange_ms=(time.monotonic() - started) * 1000,
                            server_us=list(self.client.last_timings),
                            send=transport_stat(self.client, 'last_send'),
                            receive=transport_stat(self.client, 'last_receive'))

  def _verified_output(self):
    from openpilot.selfdrive.modeld.jetlink.daemon import require_selected
    if self.client.last_output is None:
      return None
    if self._verified_frame == self.client.output_frame:
      return None
    state = self.client.last_state
    if not isinstance(state, dict) or 'loaded' not in state:
      # Older Mac apps put only temperatures in INFER_RESP. Keep their
      # identity check, bounded by this output's remaining hard deadline.
      left = self.timeout - (time.monotonic() - self.client.output_at)
      if left <= 0:
        raise TimeoutError('Jetlink model identity deadline exceeded')
      state = self.client.state(timeout=left)
    require_selected(state, self.spec)
    self._verified_frame = self.client.output_frame
    self.output_at = self.client.output_at
    self.timings = self.client.last_timings
    return self.client.last_output.copy()  # sunnypilot's parser mutates arrays

  def take_ready(self):
    with self._io_lock:
      if self._closed.is_set():
        raise ConnectionError('USB lease closing')
      self.client.drain()
      return self._verified_output()

  def infer_frame(self, image, packed, frame, reset, deadline, after_enqueue=None):
    """Camera-thread AIO send/soft wait, following zoompilot's frame pipeline.

    io_submit owns the input bytes before returning. No worker retains an
    image buffer that the next GPU warp could overwrite.
    """
    from openpilot.selfdrive.modeld.jetlink.daemon import transport_stat
    if image.dtype != np.uint8 or image.shape != self.spec.warped_shape or not image.flags.c_contiguous:
      raise ValueError('invalid warped image')
    if packed.dtype != np.float32 or packed.size != self.spec.packed_nelem or not packed.flags.c_contiguous or not np.all(np.isfinite(packed)):
      raise ValueError('invalid recurrent input')
    with self._io_lock:
      if self._closed.is_set():
        raise ConnectionError('USB lease closing')
      started = time.monotonic()
      try:
        seq = self.client.infer_begin(image, packed, frame, reset=reset, want_state=True,
                                      skip_if_busy=self.client.last_output is not None)
        self.sent = seq is not None
        if after_enqueue is not None:
          after_enqueue()
        if seq is None:
          return None
        if self.hud is not None:
          # Queue behind this model request, before spending the frame budget
          # waiting for its reply. Post-reply-only HUD can starve on slow models.
          model_send = self.client.t.last_send
          try:
            self.hud.send(self.client, deadline=deadline, after_seq=seq)
          finally:
            self.client.t.last_send = model_send
        hold = max(0., deadline - time.monotonic()) if self.client.last_output is not None else None
        output = self.client.infer_end(seq, hold=hold)
        verified = self._verified_output() if output is not None else None
        if verified is not None and self.hud is not None:
          # Preserve inference's send diagnostics; HUD has its own counters.
          model_send = self.client.t.last_send
          try:
            self.hud.send(self.client, deadline=deadline)
          finally:
            self.client.t.last_send = model_send
        return verified
      finally:
        self.metrics = dict(frame=frame, output_frame=self.client.output_frame,
                            pending=len(self.client.pending), sent=self.sent,
                            exchange_ms=(time.monotonic() - started) * 1000,
                            server_us=list(self.client.last_timings),
                            send=transport_stat(self.client, 'last_send'), receive=transport_stat(self.client, 'last_receive'))

  def close(self):
    if self._closed.is_set():
      return
    self._closed.set()
    threading.Thread(target=self._cleanup, name='jetlink-usb-release', daemon=True).start()

  def _cleanup(self):
    try:
      background_thread()
      # Wait outside the camera loop for its bounded outstanding exchange.
      with self._io_lock:
        if self.hud is not None:
          self.hud.close()
        if self.transport is not None:
          self._rpc('drop')  # cancels outstanding kernel reads/writes
          self.transport._closing = True
          # Already unbound; a cleanup must not rebind before joining reader.
          self.transport._bounce = None
          reader = self.transport._reader
          self.transport.close()
          if self.transport.cleanup_incomplete or (reader is not None and reader.is_alive()):
            raise RuntimeError('USB endpoint cleanup incomplete')
          self._rpc('release', clean=True)
    except Exception:
      log.exception('Direct USB cleanup failed; owner will keep endpoints unavailable')
    finally:
      self.control.close()
      self.cleaned.set()

