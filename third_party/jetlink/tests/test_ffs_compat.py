"""HKG's v2/v3 framing must survive the upstream AIO transport port."""
import pytest

from aio_fakes import FakeAio
from jetlink import protocol as P
from jetlink.transport.base import LinkError
from jetlink.transport.ffs import FfsTransport, QUEUED_LIMIT


@pytest.mark.parametrize('version', [2, 3])
def test_aio_preserves_version_payload_flags_and_locks_stream(version):
  t = FfsTransport.__new__(FfsTransport)
  t._prepare('/unused', None)
  t._ensure_epfiles = lambda: None
  t.protocol_version = version
  t._aio = a = FakeAio(None, QUEUED_LIMIT // t.write_chunk)
  payload = bytes(range(256)) * 1536
  flags = int(P.Flag.RESET_QUEUES | P.Flag.WANT_STATE)
  if version == 3:
    flags |= P.Flag.WANT_HIDDEN
  t.send(P.Msg.INFER_REQ, 19, (b'', payload[:123], payload[123:]), flags=flags)
  header = P.unpack_header(bytes(a.wire[:P.HEADER_SIZE]), version=version)
  assert header[1:6] == (version, P.Msg.INFER_REQ, 19, flags, len(payload))
  assert bytes(a.wire[P.HEADER_SIZE:P.HEADER_SIZE + len(payload)]) == payload
  assert set(a.requests) == {8192}
  assert a.submits == 1
  with pytest.raises(ValueError, match='locked'):
    t.protocol_version = 3 if version == 2 else 2
  t._settle_writes()
  assert a.closed


def test_zero_submit_abandons_instead_of_spinning():
  t = FfsTransport.__new__(FfsTransport)
  t._prepare('/unused', None)
  t._ensure_epfiles = lambda: None
  t._udc_note = lambda: ''
  t._aio = a = FakeAio(None, QUEUED_LIMIT // t.write_chunk)
  a.submit = lambda *args: 0
  with pytest.raises(LinkError, match='no progress'):
    t.send(P.Msg.PING, 1)
  with pytest.raises(LinkError, match='no progress'):
    t.send(P.Msg.PING, 2)
  assert t._submitted == 0
  t._settle_writes()


def test_unfinished_aio_cleanup_is_reported_to_the_owner(monkeypatch):
  from jetlink.transport import ffs
  monkeypatch.setattr(ffs, 'CLOSE_FLUSH', 0.001)
  monkeypatch.setattr(ffs, 'ABORT_DRAIN', 0.001)
  t = FfsTransport.__new__(FfsTransport)
  t._prepare('/unused', None)
  t._ensure_epfiles = lambda: None
  t._aio = a = FakeAio(None, QUEUED_LIMIT // t.write_chunk)
  a.holding = True
  t.send(P.Msg.PING, 1)
  t.unbind = lambda *args: None
  t._close_fds = lambda *args: None
  t.close()
  assert t.cleanup_incomplete
  assert not a.closed, 'io_destroy must not block on unfinished requests'
