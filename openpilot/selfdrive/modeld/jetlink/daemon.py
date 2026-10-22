"""USB bulk owner; no USB setup or model loading on modeld's realtime thread.

This port uses protocol 3, a compatible preloaded App-selected engine, and no HUD,
navigation, Wi-Fi provisioning or phone networking side channels.
"""
import fcntl
import json
import logging
import os
import re
from pathlib import Path
import socket
import subprocess
import time

import numpy as np

from openpilot.common.jetlink_status import STATUS
from openpilot.selfdrive.modeld.jetlink import VENDOR
from openpilot.selfdrive.modeld.jetlink.link import SOCKET, SPEC, REQUEST, REPLY, PacketReader, send, send_parts
from openpilot.selfdrive.modeld.jetlink.contracts import model_name, validate_model_spec
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


def publish(state, spec=None, **extra):
  tmp = STATUS.with_suffix('.tmp')
  tmp.write_text(json.dumps(dict(state=state, updated=time.monotonic(),
                                model=model_name(spec) if spec else 'App selection',
                                sha256=spec.sha256 if spec else None, **extra)))
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


class ModelChanged(RuntimeError):
  pass


def require_selected(current, spec):
  if not isinstance(current, dict) or current.get('loaded') != spec.sha256:
    raise ModelChanged('Mac model changed or did not report its identity; reconnect with ignition off')


def check_loaded(client, spec):
  require_selected(client.state(), spec)


def prepare_selected(client, peer, offroad):
  if not isinstance(peer, dict) or type(peer.get('protocol')) is not int or peer['protocol'] != 3:
    raise ValueError('Mac Jetlink protocol 3 is required')
  selected = peer.get('loaded')
  if not isinstance(selected, str) or re.fullmatch('[0-9a-f]{64}', selected) is None:
    raise ValueError('Select and prepare a driving model in the Jetlink App, then reconnect with ignition off')
  if not offroad():
    raise RuntimeError('Turn ignition off before changing the Jetlink model')
  current = client.state()
  if not isinstance(current, dict) or current.get('loaded') != selected:
    raise ModelChanged('App selection changed during handshake; reconnect offroad')
  try:
    spec = validate_model_spec(client.ensure_engine(selected, SPEC.nbytes if selected == SPEC.sha256 else 0,
                                                  frame_skip=4, build_timeout=30, should_stop=lambda: not offroad()))
    if spec.sha256 != selected:
      raise ModelChanged('The prepared engine does not match the App selection')
    if not offroad():
      raise RuntimeError('Ignition started during model preparation; reconnect offroad')
    check_loaded(client, spec)
    return spec
  finally:
    client.progress_cb = None
    client._should_stop = lambda: False


def serve_local(listener, client):
  spec = validate_model_spec(client.spec)
  last_status = last_ping = 0.
  while host_attached():
    now = time.monotonic()
    if now - last_status >= 1:
      publish('ready', spec=spec, timings=list(client.last_timings))
      last_status = now
    try:
      connection, _ = listener.accept()
    except TimeoutError:
      if time.monotonic() - last_ping >= 2:
        check_loaded(client, spec)
        last_ping = time.monotonic()
      continue
    with connection:
      connection.settimeout(.5)
      reader = PacketReader(REQUEST.size + spec.warped_nbytes + spec.packed_nbytes)
      try:
        check_loaded(client, spec)
        send(connection, json.dumps({'spec': spec.to_dict()}).encode())
        while host_attached():
          if time.monotonic() - last_status >= 1:
            publish('ready', spec=spec, timings=list(client.last_timings))
            last_status = time.monotonic()
          try:
            request = reader.receive(connection)
          except TimeoutError:
            check_loaded(client, spec)
            continue
          if len(request) != REQUEST.size + spec.warped_nbytes + spec.packed_nbytes:
            raise ValueError('Invalid local inference packet size')
          frame, reset, _ = REQUEST.unpack_from(request)
          if reset not in (0, 1):
            raise ValueError('Invalid reset flag')
          images = np.frombuffer(request, np.uint8, spec.warped_nbytes, REQUEST.size).reshape(spec.warped_shape)
          packed = np.frombuffer(request, np.float32, spec.packed_nelem, REQUEST.size + spec.warped_nbytes)
          if not np.all(np.isfinite(packed)):
            raise ValueError('Nonfinite local model input')
          output = client.infer(images, packed, frame, reset=bool(reset), want_state=True)
          require_selected(client.last_state, spec)
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
        if (Path('/sys/class/udc') / udc / 'current_speed').read_text().strip() not in ('super-speed', 'super-speed-plus'):
          raise ValueError('This port requires a USB 3 data cable at SuperSpeed')
        offroad = lambda: params.get_bool('IsOffroad') and not params.get_bool('IsOnroad')
        spec = prepare_selected(client, peer, offroad)
        for frame in range(3):
          if not offroad():
            raise RuntimeError('Ignition started during warmup; reconnect offroad')
          client.infer(np.zeros(spec.warped_shape, np.uint8), np.zeros(spec.packed_nelem, np.float32),
                       frame, reset=True, want_state=True)
          require_selected(client.last_state, spec)
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
