"""Protocol 3 over the iPhone/iPad's USB NCM network, never a Wi-Fi listener."""
import ipaddress
import socket
import time

from jetlink.transport.base import LinkError, udc_speed, usb_link_info
from jetlink.transport.tcp import TcpTransport

ADDRESS = "192.168.60.1"
PORT = 5599
NETWORK = ipaddress.ip_network("192.168.60.0/24")

def cable_peer(local, peer):
  try:
    address = ipaddress.ip_address(peer[0])
    return local[0] == ADDRESS and address in NETWORK and 2 <= int(str(address).rsplit(".", 1)[1]) <= 254
  except (ValueError, TypeError, IndexError):
    return False

class CableTransport(TcpTransport):
  def __init__(self, sock):
    if not cable_peer(sock.getsockname(), sock.getpeername()):
      sock.close()
      raise LinkError("Jetlink iOS connection is not on the USB cable subnet")
    super().__init__(sock)
    self.protocol_version = 3

  def send(self, msg_type, seq, parts=(), flags=0, timeout=None):
    # Empty iovecs can otherwise remain after a complete sendmsg and make
    # the older stream transport mistake a zero-byte write for disconnect.
    parts = tuple(part for part in parts if memoryview(part).nbytes)
    return super().send(msg_type, seq, parts, flags, timeout)

  def link_info(self):
    return usb_link_info("cable", udc_speed())

  def close(self):
    try:
      self.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    super().close()

def accept_phone(offroad, present, announce, timeout=60.0):
  """The phone's server dials us; we remain the wire client after accepting."""
  listener = TcpTransport.listen(ADDRESS, PORT)
  listener.settimeout(.25)
  deadline = time.monotonic() + timeout
  try:
    last_announce = 0.0
    while time.monotonic() < deadline:
      if time.monotonic() - last_announce >= 1:
        announce("waiting", error="Open Jetlink on your iPhone/iPad, allow Local Network and keep the app on screen")
        last_announce = time.monotonic()
      if not offroad():
        raise RuntimeError("Turn ignition off before connecting iPhone/iPad")
      if not present():
        raise ConnectionError("USB host disconnected")
      try:
        sock, _ = listener.accept()
      except TimeoutError:
        continue
      if not offroad():
        sock.close()
        raise RuntimeError("Ignition started while iPhone/iPad connected")
      if not cable_peer(sock.getsockname(), sock.getpeername()):
        sock.close()
        continue
      return CableTransport(sock)
    raise TimeoutError("No iPhone/iPad connection on USB; check Local Network permission and USB 3 cable/hub")
  finally:
    listener.close()
