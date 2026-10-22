"""Real local lease sockets, mocked USB only; no camera tensors through IPC."""
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.selfdrive.modeld.jetlink import direct, daemon, link
from openpilot.selfdrive.modeld.jetlink.contracts import DEFAULT_SPEC


class LeaseTests(unittest.TestCase):
  def setUp(self):
    ctx = patch.object(direct, 'wait_peer_exit', return_value=False)
    self.wait_exit = ctx.start()
    self.addCleanup(ctx.stop)
    self.events = []
    self.owner = SimpleNamespace(session=None, failed=None, udc='udc',
                                 owner=SimpleNamespace(mount='/dev/fake', gadget='/config/fake', lendable=True),
                                 drop=lambda: self.events.append('drop'),
                                 restore=lambda: self.events.append('restore'),
                                 bounce=lambda: self.events.append('bounce'))
    self.server, self.client = socket.socketpair()
    self.client.settimeout(2.)
    self.errors = []
    self.addCleanup(self.server.close)
    self.addCleanup(self.client.close)
    def serve():
      try:
        direct.serve_lease(self.server, self.owner, DEFAULT_SPEC, Mock())
      except Exception as exc:
        self.errors.append(exc)
    self.thread = threading.Thread(target=serve, daemon=True)
    self.thread.start()

  def borrow(self):
    direct.packet(self.client, {'op': 'borrow', 'version': 1})
    grant = direct.read_packet(self.client)
    self.assertEqual(grant['spec'], DEFAULT_SPEC.to_dict())
    self.assertEqual(grant['udc'], 'udc')

  def command(self, op, **kw):
    direct.packet(self.client, dict(op=op, **kw))
    self.assertEqual(direct.read_packet(self.client), {'ok': True})

  def finish(self):
    self.thread.join(2.)
    self.assertFalse(self.thread.is_alive())

  def test_clean_handoff_drops_then_rebinds_only_after_release(self):
    self.borrow()
    self.command('bounce')
    self.command('drop')
    self.assertEqual(self.events, ['bounce', 'drop'])
    self.command('release', clean=True)
    self.finish()
    self.assertEqual(self.events, ['bounce', 'drop', 'restore'])
    self.assertIsNone(self.owner.failed)
    self.assertEqual(self.errors, [])

  def test_disappearing_borrower_stays_unbound_and_blocks_reuse(self):
    self.borrow()
    self.client.close()
    self.finish()
    self.assertEqual(self.events, ['drop'])
    self.assertIn('cleanup', self.owner.failed)

  def test_modeld_process_exit_reclaims_lease_for_next_ignition(self):
    self.wait_exit.return_value = True
    self.borrow()
    self.client.close()
    self.finish()
    self.assertEqual(self.events, ['drop', 'restore'])
    self.assertIsNone(self.owner.failed)

  def test_unbind_failure_blocks_reuse(self):
    self.borrow()
    self.owner.drop = Mock(side_effect=RuntimeError('unbind failed'))
    self.client.close()
    self.finish()
    self.assertIn('unbind failed', self.owner.failed)
    self.assertNotIn('restore', self.events)

  def test_release_without_dropping_cannot_rebind(self):
    self.borrow()
    direct.packet(self.client, {'op': 'release', 'clean': True})
    self.finish()
    self.assertEqual(self.events, ['drop'])
    self.assertIsNotNone(self.owner.failed)

  def test_canceled_before_grant_does_not_poison_owner(self):
    self.client.close()
    self.finish()
    self.assertEqual(self.events, [])
    self.assertIsNone(self.owner.failed)

  def test_cancel_after_request_before_grant_keeps_owner_reusable(self):
    # Modeld can cancel its join while jetlinkd closes the idle USB session.
    # Its borrow request remains readable, but replying to the closed socket
    # fails. No complete grant means that client cannot have opened endpoints.
    with patch.object(direct, 'packet', side_effect=BrokenPipeError('canceled grant')):
      direct.send(self.client, b'{"op":"borrow","version":1}')
      self.finish()
    self.assertEqual(len(self.errors), 1)
    self.assertIsInstance(self.errors[0], BrokenPipeError)
    self.assertEqual(self.events, [])
    self.assertIsNone(self.owner.failed)
    self.wait_exit.assert_not_called()

  def test_partial_grant_failure_does_not_create_an_endpoint_lease(self):
    def partial_grant(sock, value):
      sock.sendall(b'\x40\x00\x00\x00{"version":1')
      raise BrokenPipeError('partial grant')
    with patch.object(direct, 'packet', side_effect=partial_grant):
      direct.send(self.client, b'{"op":"borrow","version":1}')
      self.finish()
    self.assertIsInstance(self.errors[0], BrokenPipeError)
    self.assertEqual(self.events, [])
    self.assertIsNone(self.owner.failed)

  def test_incomplete_cleanup_never_rebinds(self):
    self.borrow()
    self.command('drop')
    direct.packet(self.client, {'op': 'release', 'clean': False})
    self.finish()
    self.assertNotIn('restore', self.events)
    self.assertIsNotNone(self.owner.failed)


class DirectClientTests(unittest.TestCase):
  def connect(self, peer=None, speed='super-speed'):
    self.cleanup_done = threading.Event()
    cleanup = direct.DirectClient._cleanup
    def cleanup_and_signal(client):
      try:
        cleanup(client)
      finally:
        self.cleanup_done.set()
    server, client = socket.socketpair()
    self.addCleanup(server.close)
    self.addCleanup(client.close)
    connected = Mock(wraps=client)
    connected.connect = Mock()  # socketpair is already connected
    self.events, self.errors = [], []
    self.owner = SimpleNamespace(session=None, failed=None, udc='udc',
                                 owner=SimpleNamespace(mount='/dev/fake', gadget='/config/fake', lendable=True),
                                 drop=lambda: self.events.append('drop'),
                                 restore=lambda: self.events.append('restore'), bounce=lambda: True)
    def serve():
      with server as sock:
        try:
          direct.serve_lease(sock, self.owner, DEFAULT_SPEC, Mock())
        except Exception as exc:
          self.errors.append(exc)
    self.thread = threading.Thread(target=serve, daemon=True)
    self.thread.start()
    self.t = SimpleNamespace(protocol_version=None, _reader=None, cleanup_incomplete=False,
                             close=lambda: self.events.append('close'), last_send={}, last_receive={})
    self.remote = Mock(t=self.t, last_timings=(32000, 20, 32020))
    self.remote.hello.return_value = {'loaded': DEFAULT_SPEC.sha256} if peer is None else peer
    self.remote.state.return_value = {'loaded': DEFAULT_SPEC.sha256}
    self.output = np.zeros(DEFAULT_SPEC.output_nelem, np.float32)
    def infer(*args, **kw):
      self.remote.last_state = {'loaded': DEFAULT_SPEC.sha256}
      return self.output
    self.remote.infer.side_effect = infer
    for ctx in (patch.object(direct.socket, 'socket', return_value=connected),
                patch.object(direct.DirectClient, '_cleanup', cleanup_and_signal),
                patch.object(direct, 'wait_peer_exit', return_value=False),
                patch.object(direct.FfsTransport, 'borrowed', return_value=self.t),
                patch.object(direct, 'JetlinkClient', return_value=self.remote),
                patch.object(daemon, 'prepare_selected', return_value=DEFAULT_SPEC),
                patch.object(daemon, 'read_port', return_value=speed)):
      ctx.start()
      self.addCleanup(ctx.stop)
    return direct.DirectClient('/test/lease')

  def finish(self, client):
    client.close()
    self.assertTrue(client.cleaned.wait(2.))
    self.thread.join(2.)
    self.assertFalse(self.thread.is_alive())

  def test_tensors_go_to_usb_without_socket_forwarding(self):
    c = self.connect()
    image = np.zeros(DEFAULT_SPEC.warped_shape, np.uint8)
    packed = np.zeros(DEFAULT_SPEC.packed_nelem, np.float32)
    output = c.infer(image, packed, 7, True)
    self.assertIs(output, self.output)
    self.assertIs(self.remote.infer.call_args.args[0], image)
    self.assertEqual(c.metrics['frame'], 7)
    self.assertEqual(c.metrics['server_us'], [32000, 20, 32020])
    self.finish(c)
    self.assertEqual(self.events, ['drop', 'close', 'restore'])
    self.assertEqual(self.errors, [])

  def test_close_does_not_wait_on_camera_thread_and_stops_new_frames(self):
    c = self.connect()
    c._io_lock.acquire()
    try:
      start = time.monotonic()
      c.close()
      self.assertLess(time.monotonic() - start, .1)
      self.assertFalse(c.cleaned.is_set())
    finally:
      c._io_lock.release()
    self.finish(c)
    with self.assertRaises(ConnectionError):
      c.infer(np.zeros(DEFAULT_SPEC.warped_shape, np.uint8), np.zeros(DEFAULT_SPEC.packed_nelem, np.float32), 8)

  def test_failed_cleanup_does_not_offer_endpoints_again(self):
    c = self.connect()
    self.t.cleanup_incomplete = True
    with self.assertLogs(direct.log, level='ERROR'):
      self.finish(c)
    self.assertNotIn('restore', self.events)
    self.assertIsNotNone(self.owner.failed)

  def test_model_identity_change_is_rejected_before_returning_output(self):
    c = self.connect()
    def changed(*args, **kw):
      self.remote.last_state = {'loaded': '0' * 64}
      return self.output
    self.remote.infer.side_effect = changed
    with self.assertRaises(daemon.ModelChanged):
      c.infer(np.zeros(DEFAULT_SPEC.warped_shape, np.uint8), np.zeros(DEFAULT_SPEC.packed_nelem, np.float32), 1)
    self.finish(c)

  def test_model_changed_during_handoff_releases_lease(self):
    with self.assertRaisesRegex(ValueError, 'model changed'):
      self.connect(peer={'loaded': '0' * 64})
    self.assertTrue(self.cleanup_done.wait(2.))
    self.thread.join(2.)
    self.assertFalse(self.thread.is_alive())
    self.assertEqual(self.events, ['drop', 'close', 'restore'])

  def test_usb2_after_handoff_releases_lease(self):
    with self.assertRaisesRegex(ValueError, 'SuperSpeed'):
      self.connect(speed='high-speed')
    self.assertTrue(self.cleanup_done.wait(2.))
    self.thread.join(2.)
    self.assertFalse(self.thread.is_alive())
    self.assertEqual(self.events, ['drop', 'close', 'restore'])


class ClientSelectionTests(unittest.TestCase):
  def test_peer_reuse_and_zombie_release_but_live_or_uninterruptible_do_not(self):
    for current, expected in ((None, True), (('new', 'S'), True), (('old', 'Z'), True),
                              (('old', 'S'), False), (('old', 'D'), False)):
      with self.subTest(current=current), patch.object(direct, 'process_identity', return_value=current):
        self.assertEqual(direct.wait_peer_exit(123, ('old', 'S'), timeout=0), expected)

  def test_process_stat_parses_start_tick_after_command_with_parentheses(self):
    stat = '123 (modeld (worker)) S ' + ' '.join(str(i) for i in range(4, 23))
    with patch.object(direct.Path, 'read_text', return_value=stat):
      self.assertEqual(direct.process_identity(123), ('22', 'S'))
    with patch.object(direct.Path, 'read_text', side_effect=FileNotFoundError):
      self.assertIsNone(direct.process_identity(123))

  def test_unreadable_peer_identity_does_not_allow_endpoint_reuse(self):
    for error in (PermissionError(), IndexError(), ValueError()):
      with self.subTest(error=error), patch.object(direct, 'process_identity', side_effect=error):
        self.assertFalse(direct.wait_peer_exit(123, ('old', 'S'), timeout=0))

  def test_only_advertised_mac_direct_path_uses_lease(self):
    for mode, wanted in (('usb-direct', True), (None, False)):
      with self.subTest(mode=mode), patch.object(link, 'state', return_value={'transport': mode}), \
          patch.object(direct, 'DirectClient') as mac, patch.object(link, 'Client') as ipc, \
          patch.object(link, 'AsyncClient') as wrapped, patch.object(link, 'background_thread'):
        f = link.Future()
        link.ClientConnection._connect(f)
        self.assertIs(f.result(), mac.return_value if wanted else wrapped.return_value)
        self.assertEqual(mac.called, wanted)
        self.assertEqual(ipc.called, not wanted)
