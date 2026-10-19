"""USB bulk owner; no USB setup or model loading on modeld's realtime thread.

This first port uses protocol 3, a preloaded Cinque v2 app engine, and no HUD,
navigation, Wi-Fi provisioning or phone networking side channels.
"""
import fcntl
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import time

import numpy as np

from openpilot.common.jetlink_status import STATUS
from openpilot.selfdrive.modeld.jetlink import VENDOR
from openpilot.selfdrive.modeld.jetlink.link import SOCKET, SPEC, REQUEST, REPLY, PacketReader, send, send_parts
from openpilot.selfdrive.modeld.jetlink.contracts import validate_model_spec
from jetlink.client import JetlinkClient
from jetlink.transport.ffs import FfsTransport

GADGET = '/sys/kernel/config/usb_gadget/jetlink'
log = logging.getLogger('sunnypilot.jetlink')


class UnsafeCleanupError(RuntimeError):
  pass


class SafeTransport(FfsTransport):
  def close(self):
    reader = getattr(self, '_reader', None)
    watchdog = getattr(getattr(self, '_write_guard', None), 'thread', None)
    super().close()
    if any(t is not None and t.is_alive() for t in (reader, watchdog)):
      raise UnsafeCleanupError('USB cleanup left an active thread; reboot offroad before retrying')
    if Path(GADGET, 'UDC').read_text().strip():
      raise UnsafeCleanupError('USB gadget remains bound; reboot offroad before retrying')


def publish(state, **extra):
  tmp = STATUS.with_suffix('.tmp')
  tmp.write_text(json.dumps(dict(state=state, updated=time.monotonic(), model='Cinque v2', **extra)))
  os.replace(tmp, STATUS)


def host_attached():
  try:
    return Path('/sys/class/power_supply/usb/typec_mode').read_text().strip().startswith('Source attached')
  except OSError:
    return False


def cleanup(transport):
  if transport is None:
    return
  reader = getattr(transport, '_reader', None)
  watchdog = getattr(getattr(transport, '_write_guard', None), 'thread', None)
  transport.close()
  if any(t is not None and t.is_alive() for t in (reader, watchdog)):
    raise UnsafeCleanupError('USB cleanup left an active thread; reboot offroad before retrying')
  if Path(GADGET, 'UDC').read_text().strip():
    raise UnsafeCleanupError('USB gadget remains bound; reboot offroad before retrying')


def check_loaded(client):
  current = client.state()
  if not isinstance(current, dict) or current.get('loaded') != SPEC.sha256:
    raise ValueError('Mac engine changed; select Cinque v2 again with ignition off')


def serve_local(listener, client):
  last_status = last_ping = 0.
  while host_attached():
    now = time.monotonic()
    if now - last_status >= 1:
      publish('ready', sha256=SPEC.sha256, timings=list(client.last_timings))
      last_status = now
    try:
      connection, _ = listener.accept()
    except TimeoutError:
      if time.monotonic() - last_ping >= 2:
        check_loaded(client)
        last_ping = time.monotonic()
      continue
    with connection:
      connection.settimeout(.5)
      reader = PacketReader(REQUEST.size + SPEC.warped_nbytes + SPEC.packed_nbytes)
      try:
        check_loaded(client)
        send(connection, json.dumps({'spec': SPEC.to_dict()}).encode())
        while host_attached():
          if time.monotonic() - last_status >= 1:
            publish('ready', sha256=SPEC.sha256, timings=list(client.last_timings))
            last_status = time.monotonic()
          try:
            request = reader.receive(connection)
          except TimeoutError:
            check_loaded(client)
            continue
          if len(request) != REQUEST.size + SPEC.warped_nbytes + SPEC.packed_nbytes:
            raise ValueError('Invalid local inference packet size')
          frame, reset, _ = REQUEST.unpack_from(request)
          if reset not in (0, 1):
            raise ValueError('Invalid reset flag')
          images = np.frombuffer(request, np.uint8, SPEC.warped_nbytes, REQUEST.size).reshape(SPEC.warped_shape)
          packed = np.frombuffer(request, np.float32, SPEC.packed_nelem, REQUEST.size + SPEC.warped_nbytes)
          if not np.all(np.isfinite(packed)):
            raise ValueError('Nonfinite local model input')
          output = client.infer(images, packed, frame, reset=bool(reset))
          send_parts(connection, REPLY.pack(frame, *client.last_timings), output)
      except (ConnectionError, BrokenPipeError, ValueError) as exc:
        log.info('Local client ended: %s', exc)
    last_ping = 0.


def main():
  from openpilot.common.params import Params
  from openpilot.selfdrive.modeld.helpers import chestnut_present
  from openpilot.sunnypilot.models.helpers import get_active_model_runner
  from openpilot.cereal import custom

  logging.basicConfig(level=logging.INFO)
  os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  os.sched_setaffinity(0, set(range(min(4, os.cpu_count() or 1))))
  params = Params()
  lock = open('/dev/shm/sunnypilot-jetlink.lock', 'w')
  fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  Path(SOCKET).unlink(missing_ok=True)
  listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  listener.bind(SOCKET)
  os.chmod(SOCKET, 0o600)
  listener.listen(1)
  listener.settimeout(.05)
  blocked = None
  try:
    while True:
      if blocked:
        publish('blocked', error=blocked)
        time.sleep(1)
        continue
      if get_active_model_runner(params) != custom.ModelManagerSP.Runner.stock or chestnut_present():
        publish('unsupported', error='Select the default small model and disconnect Chestnut before using Jetlink')
        time.sleep(1)
        continue
      # Setup/re-enumeration and every engine handshake require actual ignition
      # off, even if the UI has an always-offroad override.
      if params.get_bool('IsOnroad') or not params.get_bool('IsOffroad'):
        publish('waiting', error='Turn ignition off to connect the Mac Jetlink app')
        time.sleep(1)
        continue
      if not host_attached():
        publish('waiting', error='Connect a USB 3 data cable and start Jetlink on the Mac')
        time.sleep(1)
        continue
      transport = None
      try:
        publish('connecting')
        functions = Path(GADGET, 'functions')
        if functions.exists() and any(p.name != 'ffs.jetlink' for p in functions.iterdir()):
          raise RuntimeError('Existing Jetlink gadget has other USB functions; remove the previous installation before using this port')
        setup = VENDOR.parents[1] / 'tools' / 'jetlink' / 'setup_gadget.sh'
        command = ['bash', str(setup)]
        if os.geteuid() != 0:
          command = ['sudo', '-n'] + command
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if result.returncode:
          raise RuntimeError('USB setup failed: ' + result.stderr[-300:])
        udc = next(Path('/sys/class/udc').iterdir()).name
        transport = SafeTransport('/dev/ffs-jetlink', gadget=GADGET, udc=udc)
        transport.protocol_version = 3
        client = JetlinkClient(transport, name='sunnypilot-jetlink')
        peer = client.hello()
        if peer.get('protocol') != 3 or peer.get('loaded') != SPEC.sha256:
          raise ValueError('In Mac Jetlink select and prepare Cinque v2 (09d080f36965), then reconnect offroad')
        if (Path('/sys/class/udc') / udc / 'current_speed').read_text().strip() not in ('super-speed', 'super-speed-plus'):
          raise ValueError('This port requires a USB 3 data cable at SuperSpeed')
        if params.get_bool('IsOnroad'):
          raise RuntimeError('Ignition started during setup; retry with ignition off')
        validate_model_spec(client.ensure_engine(SPEC.sha256, SPEC.nbytes, frame_skip=SPEC.frame_skip,
                                                build_timeout=30, should_stop=lambda: params.get_bool('IsOnroad')))
        if params.get_bool('IsOnroad'):
          raise RuntimeError('Ignition started before warmup completed')
        client.progress_cb = None
        client._should_stop = lambda: False
        for frame in range(3):
          if params.get_bool('IsOnroad'):
            raise RuntimeError('Ignition started during warmup; reconnect offroad')
          client.infer(np.zeros(SPEC.warped_shape, np.uint8), np.zeros(SPEC.packed_nelem, np.float32), frame, reset=True)
        serve_local(listener, client)
      except UnsafeCleanupError as exc:
        blocked = str(exc)[:300]
        publish('blocked', error=blocked)
      except Exception as exc:
        log.exception('Jetlink session ended')
        publish('retrying', error=str(exc)[:300])
      finally:
        try:
          cleanup(transport)
        except Exception as exc:
          blocked = str(exc)[:300]
          publish('blocked', error=blocked)
      time.sleep(2)
  finally:
    listener.close()
    Path(SOCKET).unlink(missing_ok=True)
    publish('stopped')


if __name__ == '__main__':
  main()
