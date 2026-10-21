"""USB bulk owner; no USB setup or model loading on modeld's realtime thread.

This port uses protocol 3, a compatible preloaded App-selected engine, and no HUD,
navigation or Wi-Fi provisioning. iOS mode uses only a USB NCM network.
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
from openpilot.selfdrive.modeld.jetlink.cable import accept_phone, PersistentIOSGadget, wait_retry

GADGET = '/sys/kernel/config/usb_gadget/jetlink'
USB_DATA_ROLE = Path('/sys/class/usbpd/usbpd0/current_dr')
USB_TYPEC_MODE = Path('/sys/class/power_supply/usb/typec_mode')
UDC_ROOT = Path('/sys/class/udc')
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


def read_port(path):
  try:
    return path.read_text().strip()
  except OSError:
    return ''


def port_status():
  return dict(data_role=read_port(USB_DATA_ROLE).lower(), typec_mode=read_port(USB_TYPEC_MODE))


def host_attached():
  # A configured gadget proves the host is present. Power role alone cannot:
  # USB PD can swap power without swapping the host/device data roles.
  if any(read_port(path) == 'configured' for path in UDC_ROOT.glob('*/state')):
    return True
  port = port_status()
  if port['data_role'] == 'ufp':
    return True
  if port['data_role'] in ('dfp', 'none'):
    return False
  # Older kernels may omit USB PD's data-role file.
  return port['typec_mode'].startswith('Source attached')


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


def root_command(command):
  if os.geteuid() != 0:
    command = ['sudo', '-n'] + command
  result = subprocess.run(command, capture_output=True, text=True, timeout=20)
  if result.returncode:
    raise RuntimeError('USB setup failed: ' + result.stderr[-300:])
  return result


class ModelChanged(RuntimeError):
  pass


def require_selected(current, spec):
  if not isinstance(current, dict) or current.get('loaded') != spec.sha256:
    raise ModelChanged('App model changed or did not report its identity; reconnect after vehicle state is available')


def check_loaded(client, spec):
  require_selected(client.state(), spec)


def infer_selected(client, spec, images, packed, frame, reset):
  # Swift/iOS sends thermal telemetry on INFER_RESP; loaded SHA is only in
  # STATE_RESP. Never reuse an earlier frame's telemetry as its identity.
  client.last_state = None
  output = client.infer(images, packed, frame, reset=reset, want_state=True)
  state = client.last_state
  if isinstance(state, dict) and 'loaded' in state:
    require_selected(state, spec)
  else:
    check_loaded(client, spec)
  return output


def prepare_selected(client, peer, setup_allowed):
  if not isinstance(peer, dict) or type(peer.get('protocol')) is not int or peer['protocol'] != 3:
    raise ValueError('Jetlink App protocol 3 is required')
  selected = peer.get('loaded')
  if not isinstance(selected, str) or re.fullmatch('[0-9a-f]{64}', selected) is None:
    raise ValueError('Select and prepare a driving model in the Jetlink App, then reconnect after vehicle state is available')
  if not setup_allowed():
    raise RuntimeError('Fresh vehicle state is required before preparing the Jetlink model')
  current = client.state()
  if not isinstance(current, dict) or current.get('loaded') != selected:
    raise ModelChanged('App selection changed during handshake; reconnect after vehicle state is available')
  try:
    spec = validate_model_spec(client.ensure_engine(selected, SPEC.nbytes if selected == SPEC.sha256 else 0,
                                                  frame_skip=4, build_timeout=30, should_stop=lambda: not setup_allowed()))
    if spec.sha256 != selected:
      raise ModelChanged('The prepared engine does not match the App selection')
    if not setup_allowed():
      raise RuntimeError('Vehicle left the permitted setup state during model preparation')
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
          output = infer_selected(client, spec, images, packed, frame, bool(reset))
          send_parts(connection, REPLY.pack(frame, *client.last_timings), output)
      except (ConnectionError, BrokenPipeError, ValueError) as exc:
        log.info('Local client ended: %s', exc)
    last_ping = 0.


def main():
  from openpilot.common.params import Params
  from openpilot.cereal import messaging
  from openpilot.selfdrive.modeld.jetlink.ignition import connection_allowed, CONNECTION_SERVICES
  from openpilot.selfdrive.modeld.helpers import chestnut_present
  from openpilot.sunnypilot.models.helpers import get_active_model_runner
  from openpilot.cereal import custom

  logging.basicConfig(level=logging.INFO)
  os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  os.sched_setaffinity(0, set(range(min(4, os.cpu_count() or 1))))
  params = Params()
  ios_mode = params.get_bool('JetlinkIOS')  # mode changes require a reboot
  ios_setup = VENDOR.parents[1] / 'tools' / 'jetlink' / 'ios-root.sh'
  ignition_sm = messaging.SubMaster(['deviceState', 'pandaStates'])
  vehicle_sm = messaging.SubMaster(list(CONNECTION_SERVICES))
  setup_allowed = lambda: connection_allowed(params, ignition_sm, vehicle_sm)
  lock = open('/dev/shm/sunnypilot-jetlink.lock', 'w')
  fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  Path(SOCKET).unlink(missing_ok=True)
  listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  listener.bind(SOCKET)
  os.chmod(SOCKET, 0o600)
  listener.listen(1)
  listener.settimeout(.05)
  blocked = None
  ios_gadget = PersistentIOSGadget()
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
      # Keep checking real vehicle messages throughout setup. IsOffroad alone
      # never grants permission, including an always-offroad UI override.
      if not setup_allowed():
        publish('waiting', error='Waiting for fresh vehicle state before connecting Jetlink')
        time.sleep(1)
        continue
      if not host_attached():
        port = port_status()
        hint = ('C4 is USB host; use a USB 3 hub/USB-A adapter and A-to-C data cable to the Mac'
                if port['data_role'] == 'dfp' else 'Connect a USB 3 data cable and open the Jetlink App')
        publish('waiting', error=hint, usb=port)
        time.sleep(1)
        continue
      transport = None
      try:
        publish('connecting')
        functions = Path(GADGET, 'functions')
        allowed = {'ffs.jetlink', 'ncm.usb0'} if ios_mode else {'ffs.jetlink'}
        if functions.exists() and any(p.name not in allowed for p in functions.iterdir()):
          raise RuntimeError('Existing Jetlink gadget has other USB functions; remove the previous installation before using this port')
        udc = next(Path('/sys/class/udc').iterdir()).name
        if ios_mode:
          try:
            ios_gadget.ensure(
              lambda: root_command(['bash', str(ios_setup), 'gadget', '--ios']),
              lambda: SafeTransport('/dev/ffs-jetlink', gadget=GADGET, udc=udc),
              lambda: root_command(['bash', str(ios_setup), 'net']),
              lambda: Path('/dev/shm/jetlink-net').read_text().strip())
          except Exception as exc:
            blocked = 'iOS USB initialization failed; reboot before retrying: ' + str(exc)[:220]
            raise
          transport = accept_phone(setup_allowed, host_attached, publish)
        else:
          setup = VENDOR.parents[1] / 'tools' / 'jetlink' / 'setup_gadget.sh'
          root_command(['bash', str(setup)])
          transport = SafeTransport('/dev/ffs-jetlink', gadget=GADGET, udc=udc)
        if not setup_allowed():
          raise RuntimeError('Vehicle left the permitted setup state during USB setup')
        transport.protocol_version = 3
        client = JetlinkClient(transport, name='sunnypilot-jetlink-ios' if ios_mode else 'sunnypilot-jetlink')
        peer = client.hello()
        if (Path('/sys/class/udc') / udc / 'current_speed').read_text().strip() not in ('super-speed', 'super-speed-plus'):
          raise ValueError('This port requires a USB 3 data cable at SuperSpeed')
        spec = prepare_selected(client, peer, setup_allowed)
        for frame in range(3):
          if not setup_allowed():
            raise RuntimeError('Vehicle left the permitted setup state during warmup')
          infer_selected(client, spec, np.zeros(spec.warped_shape, np.uint8),
                         np.zeros(spec.packed_nelem, np.float32), frame, True)
        serve_local(listener, client)
      except UnsafeCleanupError as exc:
        blocked = str(exc)[:300]
        publish('blocked', error=blocked)
      except Exception as exc:
        log.exception('Jetlink session ended')
        publish('retrying', error=str(exc)[:300])
      finally:
        try:
          if ios_mode:
            # Keep NCM and ep0 for the daemon's lifetime. Removing/recreating
            # NCM after a model/session error can wedge the AGNOS 4.9 driver.
            if transport is not None:
              transport.close()
          else:
            cleanup(transport)
        except Exception as exc:
          blocked = str(exc)[:300]
          publish('blocked', error=blocked)
      wait_retry(host_attached)
  finally:
    if ios_gadget.owner is not None:
      cleanup(ios_gadget.owner)
    listener.close()
    Path(SOCKET).unlink(missing_ok=True)
    publish('stopped')


if __name__ == '__main__':
  main()

