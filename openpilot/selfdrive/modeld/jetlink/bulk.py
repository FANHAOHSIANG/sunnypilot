"""Resident ep0 ownership for Mac USB, with one borrowed data session.

Uses upstream FfsTransport.borrowed: ep0 and configfs outlive a session.
The existing daemon lock is the ownership lease in this integration. There
is no second process opening endpoints or forwarding USB descriptors.
"""
from pathlib import Path
import time

from jetlink.transport.ffs import FfsTransport, REBIND_SETTLE


class PersistentBulkGadget:
  def __init__(self):
    self.owner = None
    self.session = None
    self.failed = None
    self.udc = None

  def ensure(self, setup, make_owner):
    if self.failed is not None:
      raise RuntimeError('USB owner needs a reboot: ' + self.failed)
    if self.owner is not None:
      return
    try:
      setup()
      self.owner = make_owner()
      self.udc = self.owner.bound_udc
      if not self.udc or not self.owner.lendable:
        raise RuntimeError('USB owner did not bind a bare gadget')
    except Exception as exc:
      self.failed = str(exc)
      raise

  def drop(self):
    self.owner.unbind()
    if Path(self.owner.gadget, 'UDC').read_text().strip():
      raise RuntimeError('USB controller did not unbind')

  def restore(self):
    time.sleep(REBIND_SETTLE)
    self.owner.bind(self.udc)

  def bounce(self):
    self.drop()
    self.restore()
    return True

  def borrow(self):
    if self.failed is not None or self.owner is None:
      raise RuntimeError('USB owner is unavailable: ' + str(self.failed))
    if self.session is not None:
      raise RuntimeError('USB endpoints already have a session')
    session = BulkSession.borrowed(self.owner.mount, self.udc,
                                   bounce=self.bounce, owner_gadget=self.owner.gadget)
    session.owner = self
    self.session = session
    return session


class BulkSession(FfsTransport):
  def close(self):
    owner = self.owner
    if owner.session is not self:
      return
    self._closing = True
    reader = self._reader
    endpoints_open = self.ep_in >= 0 or self.ep_out >= 0
    try:
      try:
        self._settle_writes()
      finally:
        # A queued FunctionFS read keeps ep1 busy. Disable before waiting
        # for it; retain the resident owner's ep0 throughout the operation.
        if endpoints_open:
          owner.drop()
        super().close()
      if self.cleanup_incomplete or (reader is not None and reader.is_alive()):
        raise RuntimeError('USB session cleanup did not finish; reboot before retrying')
      if endpoints_open:
        owner.restore()
      owner.session = None
    except Exception as exc:
      owner.failed = str(exc)
      raise
