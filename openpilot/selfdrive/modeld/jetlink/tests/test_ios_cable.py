"""USB is simulated; real sockets exercise the vendored protocol-3 TCP stream."""
import hashlib
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from openpilot.selfdrive.modeld.jetlink import VENDOR
from openpilot.selfdrive.modeld.jetlink import cable
from jetlink import protocol as P
from jetlink.client import JetlinkClient
from jetlink.transport.tcp import TcpTransport

class CableSocket:
  """A real local socket with USB-address metadata, without needing USB hardware."""
  def __init__(self, sock):
    self.sock = sock
  def __getattr__(self, key):
    return getattr(self.sock, key)
  def getsockname(self):
    return (cable.ADDRESS, cable.PORT)
  def getpeername(self):
    return ("192.168.60.2", 40000)

class CableTests(unittest.TestCase):
  def test_only_cable_subnet_peers(self):
    for address in ("192.168.60.2", "192.168.60.254"):
      self.assertTrue(cable.cable_peer((cable.ADDRESS, 5599), (address, 12)))
    for address in ("192.168.60.0", "192.168.60.1", "192.168.60.255", "192.168.1.2", "127.0.0.1", "::1", "bad"):
      self.assertFalse(cable.cable_peer((cable.ADDRESS, 5599), (address, 12)))
    self.assertFalse(cable.cable_peer(("0.0.0.0", 5599), ("192.168.60.2", 12)))

  def test_wrong_peer_closed(self):
    sock = Mock()
    sock.getsockname.return_value = (cable.ADDRESS, 5599)
    sock.getpeername.return_value = ("192.168.1.2", 12)
    with self.assertRaises(OSError):
      cable.CableTransport(sock)
    sock.close.assert_called_once()

  def test_hello_state_and_wire_payloads(self):
    a, b = socket.socketpair()
    transport = cable.CableTransport(CableSocket(a))
    peer = TcpTransport(b)
    peer.protocol_version = 3
    errors = []
    def server():
      try:
        hello = peer.recv(2)
        self.assertEqual(hello.msg_type, P.Msg.HELLO_REQ)
        peer.send_json(P.Msg.HELLO_RESP, hello.seq, {"protocol": 3, "loaded": "a" * 64})
        state = peer.recv(2)
        self.assertEqual(state.msg_type, P.Msg.STATE_REQ)
        peer.send_json(P.Msg.STATE_RESP, state.seq, {"loaded": "a" * 64})
        for size in (0, 480, 100000):
          frame = peer.recv(2)
          self.assertEqual(frame.msg_type, P.Msg.INFER_REQ)
          self.assertEqual(bytes(frame.payload), bytes([size % 251]) * size)
          peer.send(P.Msg.INFER_RESP, frame.seq, (bytes(frame.payload),) if frame.payload.nbytes else ())
      except BaseException as exc:
        errors.append(exc)
      finally:
        peer.close()
    worker = threading.Thread(target=server, daemon=True)
    with patch.object(cable, "udc_speed", return_value="super-speed"):
      self.assertEqual(transport.link_info(), {"kind": "cable", "usb_speed": "super-speed"})
      worker.start()
      try:
        client = JetlinkClient(transport)
        self.assertEqual(client.hello()["protocol"], 3)
        self.assertEqual(client.state()["loaded"], "a" * 64)
        for size in (0, 480, 100000):
          payload = bytes([size % 251]) * size
          transport.send(P.Msg.INFER_REQ, size + 100, (payload,))
          response = transport.recv(2)
          self.assertEqual(response.msg_type, P.Msg.INFER_RESP)
          self.assertEqual(bytes(response.payload), payload)
      finally:
        transport.close()
        worker.join(3)
    self.assertFalse(worker.is_alive())
    if errors:
      raise errors[0]

  def test_listener_binds_only_cable_and_stops_on_invalid_state(self):
    listener = Mock()
    with patch.object(cable.TcpTransport, "listen", return_value=listener) as listen:
      with self.assertRaisesRegex(RuntimeError, "Fresh vehicle state"):
        cable.accept_phone(lambda: False, lambda: True, Mock())
    listen.assert_called_once_with("192.168.60.1", 5599)
    listener.close.assert_called_once()
    listener.accept.assert_not_called()

  def test_disconnect_closes_listener(self):
    listener = Mock()
    with patch.object(cable.TcpTransport, "listen", return_value=listener):
      with self.assertRaises(ConnectionError):
        cable.accept_phone(lambda: True, lambda: False, Mock())
    listener.close.assert_called_once()

  def test_state_becomes_invalid_during_accept_close_peer(self):
    listener, sock = Mock(), Mock()
    listener.accept.return_value = (sock, ("192.168.60.2", 1))
    with patch.object(cable.TcpTransport, "listen", return_value=listener):
      with self.assertRaisesRegex(RuntimeError, "Vehicle left the permitted setup state"):
        cable.accept_phone(Mock(side_effect=[True, False]), lambda: True, Mock())
    sock.close.assert_called_once()
    listener.close.assert_called_once()

  def test_accept_returns_a_protocol3_transport_and_closes_listener(self):
    listener, sock = Mock(), Mock()
    listener.accept.return_value = (sock, ("192.168.60.2", 12))
    sock.getsockname.return_value = (cable.ADDRESS, cable.PORT)
    sock.getpeername.return_value = ("192.168.60.2", 12)
    with patch.object(cable.TcpTransport, "listen", return_value=listener):
      transport = cable.accept_phone(lambda: True, lambda: True, Mock())
    self.assertEqual(transport.protocol_version, 3)
    listener.close.assert_called_once()
    transport.close()

  def test_accept_timeout_closes_listener(self):
    listener = Mock()
    with patch.object(cable.TcpTransport, "listen", return_value=listener):
      with self.assertRaises(TimeoutError):
        cable.accept_phone(lambda: True, lambda: True, Mock(), timeout=-1)
    listener.close.assert_called_once()

  def test_root_script_is_unmodified_pinned_upstream(self):
    root = Path(__file__).resolve().parents[5]
    data = (root / "tools/jetlink/ios-root.sh").read_bytes()
    digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    self.assertEqual(digest, "7ba642c23f1a7508755992abbad632c641ed5253")

  def test_daemon_keeps_bulk_owner_separate_from_phone_socket(self):
    source = (Path(__file__).resolve().parents[1] / "daemon.py").read_text()
    self.assertIn("ios_gadget.ensure(", source)
    self.assertIn("transport = accept_phone(setup_allowed, host_attached, publish)", source)
    self.assertIn("cleanup(ios_gadget.owner)", source)
    self.assertNotIn("['bash', str(ios_setup), 'teardown']", source)


class PersistentGadgetTests(unittest.TestCase):
  def test_multiple_sessions_do_not_recreate_ncm(self):
    gadget = cable.PersistentIOSGadget()
    setup, owner, net, status = Mock(), Mock(), Mock(), Mock(return_value="ok 192.168.60.1 usb1")
    for _ in range(3):
      gadget.ensure(setup, owner, net, status)
    setup.assert_called_once()
    owner.assert_called_once()
    net.assert_called_once()
    self.assertTrue(gadget.ready)

  def test_net_failure_is_latched_without_removing_or_recreating(self):
    gadget = cable.PersistentIOSGadget()
    setup, owner, net = Mock(), Mock(), Mock()
    with self.assertRaisesRegex(RuntimeError, "USB network setup failed"):
      gadget.ensure(setup, owner, net, lambda: "error: no netdev")
    with self.assertRaisesRegex(RuntimeError, "already failed"):
      gadget.ensure(setup, owner, net, lambda: "ok 192.168.60.1 usb1")
    setup.assert_called_once()
    owner.assert_called_once()
    gadget.owner.close.assert_not_called()

  def test_gadget_setup_failure_never_attempts_again(self):
    gadget = cable.PersistentIOSGadget()
    setup = Mock(side_effect=RuntimeError("NCM failed"))
    owner, net = Mock(), Mock()
    for _ in range(2):
      with self.assertRaises(RuntimeError):
        gadget.ensure(setup, owner, net, lambda: "")
    setup.assert_called_once()
    owner.assert_not_called()
    net.assert_not_called()

class FrameIdentityTests(unittest.TestCase):
  def setUp(self):
    from openpilot.selfdrive.modeld.jetlink import daemon
    self.daemon = daemon
    self.spec = Mock(sha256="a" * 64)
    self.client = Mock()
    self.client.state.return_value = {"loaded": "a" * 64}
    self.client.last_state = {"loaded": "a" * 64}

  def infer(self, telemetry):
    def result(*args, **kwargs):
      self.client.last_state = telemetry
      return "output"
    self.client.infer.side_effect = result
    return self.daemon.infer_selected(self.client, self.spec, None, None, 1, True)

  def test_swift_thermal_telemetry_uses_explicit_state_identity(self):
    self.assertEqual(self.infer({"thermal": "nominal"}), "output")
    self.client.state.assert_called_once()

  def test_swift_state_reports_changed_model(self):
    self.client.state.return_value = {"loaded": "b" * 64}
    with self.assertRaises(self.daemon.ModelChanged):
      self.infer({"thermal": "nominal"})

  def test_missing_identity_in_both_responses_rejected(self):
    self.client.state.return_value = {"thermal": "nominal"}
    with self.assertRaises(self.daemon.ModelChanged):
      self.infer({})

  def test_wrong_identity_not_masked_by_a_later_matching_state(self):
    with self.assertRaises(self.daemon.ModelChanged):
      self.infer({"loaded": "b" * 64})
    self.client.state.assert_not_called()

  def test_mac_identity_does_not_need_extra_state_request(self):
    self.assertEqual(self.infer({"loaded": "a" * 64}), "output")
    self.client.state.assert_not_called()

  def test_no_telemetry_cannot_reuse_previous_frame_identity(self):
    self.client.infer.return_value = "output"
    self.client.state.return_value = {"loaded": "b" * 64}
    with self.assertRaises(self.daemon.ModelChanged):
      self.daemon.infer_selected(self.client, self.spec, None, None, 1, False)
    self.assertIsNone(self.client.last_state)

if __name__ == "__main__":
  unittest.main()

