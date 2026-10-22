"""Independent v3 fixtures, without a USB device or neural network execution."""
import hashlib
import struct
import unittest

import numpy as np

from openpilot.selfdrive.modeld.jetlink.link import SPEC
from jetlink import protocol_v3 as V3
from jetlink.client import JetlinkClient
from jetlink.transport.base import LinkError, StreamTransport


class MemoryStream(StreamTransport):
  def __init__(self, incoming=b''):
    super().__init__(protocol_version=3)
    self.wire_protocol = V3
    self.incoming = bytearray(incoming)
    self.out = bytearray()

  def _write(self, bufs):
    data = b''.join(bufs)
    self.out.extend(data)
    return len(data)

  def _read_into(self, dest, timeout):
    n = min(len(dest), len(self.incoming), 173)
    dest[:n] = self.incoming[:n]
    del self.incoming[:n]
    return n

  def close(self):
    pass


def packet(version, kind, seq, payload):
  size = 32 + len(payload)
  flags = 128 if size % 512 == 0 else 0
  return struct.pack('<IHHIIIQ4x', 0x4B4E4C4A, version, kind, seq, flags, len(payload), 0) + payload + bytes(bool(flags))


class WireTests(unittest.TestCase):
  def test_matches_pinned_upstream_protocol3_vectors(self):
    # Vectors from upstream 24e8917's Swift wire contract and Python client.
    output = np.arange(SPEC.output_nelem, dtype=np.float32)
    response = struct.pack('<IIIII', 42, 0, 1, 2, 3) + output.tobytes() + b'{"temp_c":45}'
    stream = MemoryStream(packet(3, 9, 1, response))
    client = JetlinkClient(stream)
    client.spec = SPEC
    client.infer_begin(np.arange(SPEC.warped_nbytes, dtype=np.uint8).reshape(SPEC.warped_shape),
                       np.arange(SPEC.packed_nelem, dtype=np.float32), 42, reset=True, want_state=True)
    size = struct.unpack_from('<I', stream.out, 16)[0]
    self.assertEqual(size, 393272)
    self.assertEqual(hashlib.sha256(stream.out[32:32+size]).hexdigest(),
                     '08c60210be3ac59afb5f19059fa3042cb7efe55682c9233112146c8af6ec5930')
    result = client.infer_end(1)
    self.assertEqual(hashlib.sha256(result.tobytes()).hexdigest(),
                     '90dbeb1879dfce449d959cf618847e526cfc21e1b299544a0d0aee440ee42bce')

  def test_protocol2_header_rejected(self):
    stream = MemoryStream(packet(2, 2, 1, b'{"protocol": 3}'))
    with self.assertRaises(LinkError):
      JetlinkClient(stream).hello()

  def test_nonfinite_wire_outputs_abandon_client(self):
    output = np.zeros(SPEC.output_nelem, np.float32)
    output[0] = np.nan
    response = struct.pack('<IIIII', 1, 0, 1, 2, 3) + output.tobytes()
    stream = MemoryStream(packet(3, 9, 1, response))
    client = JetlinkClient(stream)
    client.spec = SPEC
    with self.assertRaises(LinkError):
      client.infer(np.zeros(SPEC.warped_shape, np.uint8), np.zeros(SPEC.packed_nelem, np.float32), 1)
    self.assertTrue(client.dead)


if __name__ == '__main__':
  unittest.main()
