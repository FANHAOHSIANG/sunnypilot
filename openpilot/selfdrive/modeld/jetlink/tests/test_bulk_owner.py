"""A failed exchange must not recreate ep0 or lend busy USB endpoints."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.selfdrive.modeld.jetlink import bulk


class BulkOwnerTests(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.path = Path(self.tmp.name) / 'UDC'
    self.path.write_text('test-udc\n')
    self.events = []
    self.owner = SimpleNamespace(mount=self.tmp.name, gadget=self.tmp.name,
                                 bound_udc='test-udc', lendable=True, ep0=123)
    def unbind():
      self.events.append('drop')
      self.path.write_text('')
      self.owner.bound_udc = None
    def bind(udc):
      self.events.append('bind')
      self.path.write_text(udc + '\n')
      self.owner.bound_udc = udc
    self.owner.unbind = unbind
    self.owner.bind = bind
    self.setup = Mock()
    self.make_owner = Mock(return_value=self.owner)
    self.gadget = bulk.PersistentBulkGadget()
    self.gadget.ensure(self.setup, self.make_owner)

  def test_retries_reuse_owner_and_do_not_reset_idle_usb(self):
    for _ in range(3):
      self.gadget.ensure(self.setup, self.make_owner)
      session = self.gadget.borrow()
      self.assertEqual(session.ep0, -1)
      session.close()
    self.setup.assert_called_once()
    self.make_owner.assert_called_once()
    self.assertEqual(self.owner.ep0, 123)
    self.assertEqual(self.events, [])

  def test_only_one_session_can_borrow_the_endpoints(self):
    session = self.gadget.borrow()
    with self.assertRaisesRegex(RuntimeError, 'already have'):
      self.gadget.borrow()
    session.close()
    self.gadget.borrow().close()

  def test_close_disables_before_joining_reader_then_rebinds_same_owner(self):
    session = self.gadget.borrow()
    session.ep_in = os.open('/dev/null', os.O_RDWR)
    session.ep_out = os.open('/dev/null', os.O_RDWR)
    ep_in, ep_out = session.ep_in, session.ep_out
    session._reader = Mock()
    session._reader.join.side_effect = lambda timeout: self.events.append('join')
    session._reader.is_alive.return_value = False
    with patch.object(bulk.time, 'sleep'):
      session.close()
    self.assertEqual(self.events, ['drop', 'join', 'bind'])
    self.assertEqual(self.owner.ep0, 123)
    self.assertEqual(self.path.read_text().strip(), 'test-udc')
    for fd in (ep_in, ep_out):
      with self.assertRaises(OSError):
        os.fstat(fd)
    self.gadget.borrow().close()

  def test_unfinished_writes_leave_owner_blocked_without_rebinding(self):
    session = self.gadget.borrow()
    session.cleanup_incomplete = True
    session.ep_in = os.open('/dev/null', os.O_RDWR)
    with self.assertRaisesRegex(RuntimeError, 'cleanup did not finish'):
      session.close()
    self.assertEqual(self.events, ['drop'])
    self.assertTrue(self.gadget.failed)
    with self.assertRaises(RuntimeError):
      self.gadget.borrow()
    with self.assertRaises(RuntimeError):
      self.gadget.ensure(self.setup, self.make_owner)
    self.setup.assert_called_once()

  def test_initialization_failure_is_not_repeated(self):
    gadget = bulk.PersistentBulkGadget()
    setup = Mock(side_effect=OSError('configfs failed'))
    with self.assertRaises(OSError):
      gadget.ensure(setup, self.make_owner)
    with self.assertRaisesRegex(RuntimeError, 'needs a reboot'):
      gadget.ensure(setup, self.make_owner)
    setup.assert_called_once()

  def test_unbind_must_really_release_the_controller(self):
    self.owner.unbind = lambda: None
    with self.assertRaisesRegex(RuntimeError, 'did not unbind'):
      self.gadget.drop()
