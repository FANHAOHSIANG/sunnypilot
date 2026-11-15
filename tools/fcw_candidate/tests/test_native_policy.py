"""Compile and execute the SAME new C policy header used by the candidate.
Not a complete Panda firmware or whole libsafety run.
"""
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'candidate_src'))
from fca_proxy import checksum

WRAPPER = r'''
#include "hyundai_fca_proxy.h"
static HyundaiFcaProxy state;
void reset(int enabled) { hfca_reset(&state, enabled != 0); }
int observe(unsigned int addr, unsigned int bus, const uint8_t *p, size_t n, uint32_t t) {
 return hfca_observe(&state, addr, bus, p, n, t);
}
int authorize(unsigned int addr, unsigned int bus, const uint8_t *p, size_t n, uint32_t t) {
 return hfca_authorize_tx(&state, addr, bus, p, n, t);
}
int healthy(uint32_t t) { return hfca_transport_healthy(&state, t); }
int fcw_ready(uint32_t t) { return hfca_fcw_ready(&state, t); }
uint16_t compute(const uint8_t *p) { return hfca_crc(p); }
'''


def packet(counter, setting=3):
  p = bytearray.fromhex('0000000300000002fffc010000000000')
  p[2] = counter & 255
  p[3] = setting
  p[:2] = checksum(bytes(p)).to_bytes(2,'little')
  return bytes(p)


class TestNativePolicy(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    compiler = shutil.which(os.environ.get('CC', 'cc'))
    if compiler is None:
      raise RuntimeError('C compiler required; do not silently skip native validation')
    cls.tmp = tempfile.TemporaryDirectory()
    src = Path(cls.tmp.name)/'wrapper.c'
    src.write_text(WRAPPER)
    lib = Path(cls.tmp.name)/'policy.so'
    cls.command = [compiler,'-std=c11','-Wall','-Wextra','-Werror','-fPIC','-shared',
                   '-fsanitize=undefined','-fno-sanitize-recover=undefined',
                   '-I',str(ROOT/'candidate_src'),str(src),'-o',str(lib)]
    subprocess.run(cls.command, check=True, capture_output=True, text=True)
    # Clang on Linux may leave UBSan runtime symbols in a shared test object.
    runtime = ctypes.util.find_library("ubsan") if sys.platform.startswith("linux") else None
    if runtime:
      C.CDLL(runtime, mode=C.RTLD_GLOBAL)
    cls.lib = C.CDLL(str(lib))
    cls.lib.reset.argtypes = [C.c_int]
    for name in ('observe','authorize'):
      f = getattr(cls.lib,name)
      f.argtypes = [C.c_uint,C.c_uint,C.POINTER(C.c_uint8),C.c_size_t,C.c_uint32]
      f.restype = C.c_int
    cls.lib.healthy.argtypes = cls.lib.fcw_ready.argtypes = [C.c_uint32]
    cls.lib.compute.argtypes = [C.POINTER(C.c_uint8)]
    cls.lib.compute.restype = C.c_uint16
    cls.compiler = subprocess.check_output([compiler,'--version'],text=True).splitlines()[0]

  @classmethod
  def tearDownClass(cls):
    cls.tmp.cleanup()

  def setUp(self):
    self.lib.reset(1)

  def call(self,name,data,now,addr=0x160,bus=None):
    bus = (2 if name=='observe' else 0) if bus is None else bus
    buf = (C.c_uint8*len(data)).from_buffer_copy(data)
    return bool(getattr(self.lib,name)(addr,bus,buf,len(data),now & 0xffffffff))

  def acquire(self,counter=0,setting=3,start=100000):
    for i in range(3):
      data=packet(counter+i,setting)
      self.assertEqual(self.call('observe',data,start+i*20000),i==2)
    return data,start+40000

  def test_captured_vectors_have_matching_crc(self):
    for h in ('41bab00300000002fffc010000000000','2d84000100000002fffc090000000000'):
      data=bytes.fromhex(h)
      buf=(C.c_uint8*16).from_buffer_copy(data)
      self.assertEqual(self.lib.compute(buf),int.from_bytes(data[:2],'little'))
      self.assertEqual(checksum(data),self.lib.compute(buf))

  def test_disabled_never_accepts(self):
    self.lib.reset(0)
    for i in range(5):
      self.assertFalse(self.call('observe',packet(i),i*20000))
      self.assertFalse(self.call('authorize',packet(i),i*20000))
    self.assertFalse(self.lib.healthy(100000))

  def test_no_source_no_software_frame(self):
    self.assertFalse(self.call('authorize',packet(0,1),100000))

  def test_three_valid_sequential_frames_to_acquire(self):
    p,t=self.acquire()
    self.assertTrue(self.lib.healthy(t))
    self.assertTrue(self.call('authorize',p,t+1000))

  def test_every_corrupted_bit_rejected(self):
    base=packet(9)
    for bit in range(128):
      with self.subTest(bit=bit):
        self.lib.reset(1)
        p=bytearray(base);p[bit//8]^=1<<(bit%8)
        self.assertFalse(self.call('observe',bytes(p),100000))
        self.assertFalse(self.lib.healthy(100000))

  def test_wrong_source_bus_never_arms(self):
    for bus in (0,1,3,128,130,192):
      for i in range(4):
        self.assertFalse(self.call('observe',packet(i),100000+i*20000,bus=bus))
    self.assertFalse(self.lib.healthy(160000))

  def test_wrong_message_never_arms(self):
    for i in range(4):
      self.assertFalse(self.call('observe',packet(i),100000+i*20000,addr=0x1a0))

  def test_length_mismatch_resets(self):
    for n in (0,8,15,17,24,32,64):
      self.lib.reset(1);self.acquire()
      self.assertFalse(self.call('observe',bytes(n),160000))
      self.assertFalse(self.lib.healthy(160000))

  def test_exact_bytes_only_even_valid_new_crc(self):
    p,t=self.acquire()
    for byte in range(2,16):
      q=bytearray(p);q[byte]^=4;q[:2]=checksum(bytes(q)).to_bytes(2,'little')
      self.assertFalse(self.call('authorize',bytes(q),t+1000))
    self.assertTrue(self.call('authorize',p,t+1000))

  def test_same_frame_only_once(self):
    p,t=self.acquire()
    self.assertTrue(self.call('authorize',p,t))
    self.assertFalse(self.call('authorize',p,t+1))
    newer=packet(3);self.assertTrue(self.call('observe',newer,t+20000))
    self.assertTrue(self.call('authorize',newer,t+20000))

  def test_wrong_destination_rejected(self):
    p,t=self.acquire()
    for bus in (1,2,3,128):
      self.assertFalse(self.call('authorize',p,t,bus=bus))
    self.assertTrue(self.call('authorize',p,t))

  def test_age_boundary(self):
    p,t=self.acquire()
    self.assertTrue(self.call('authorize',p,t+100000))
    self.lib.reset(1);p,t=self.acquire()
    self.assertFalse(self.call('authorize',p,t+100001))
    self.assertFalse(self.lib.fcw_ready(t+100001))

  def test_counter_repeat_requires_reacquisition(self):
    p,t=self.acquire()
    self.assertFalse(self.call('observe',p,t+20000))
    self.assertFalse(self.call('authorize',p,t+20000))
    self.assertFalse(self.call('observe',packet(3),t+40000))
    self.assertTrue(self.call('observe',packet(4),t+60000))

  def test_counter_jump_requires_reacquisition(self):
    _,t=self.acquire()
    self.assertFalse(self.call('observe',packet(8),t+20000))
    self.assertFalse(self.call('observe',packet(9),t+40000))
    self.assertTrue(self.call('observe',packet(10),t+60000))

  def test_counter_wrap_accepted(self):
    p,t=self.acquire(counter=254)
    self.assertEqual(p[2],0)
    self.assertTrue(self.call('authorize',p,t))

  def test_clock_wrap_accepted(self):
    p,t=self.acquire(start=0xffff8000)
    self.assertTrue(self.lib.healthy(t & 0xffffffff))
    self.assertTrue(self.call('authorize',p,t+2000))

  def test_gap_requires_reacquisition(self):
    p,t=self.acquire()
    self.assertFalse(self.call('observe',packet(3),t+200001))
    self.assertFalse(self.call('authorize',packet(3),t+200001))

  def test_flood_does_not_arm(self):
    for i in range(20):
      self.assertFalse(self.call('observe',packet(i),100000+i*1000))
    self.assertFalse(self.lib.healthy(119000))

  def test_raw_off_and_reserved_are_relayed_not_fabricated(self):
    for setting in range(4):
      self.lib.reset(1);p,t=self.acquire(setting=setting)
      self.assertEqual(bool(self.lib.fcw_ready(t)),setting in (2,3))
      self.assertTrue(self.call('authorize',p,t))
      self.assertEqual(p[3]&3,setting)

  def test_reset_revokes_pending_frame(self):
    p,t=self.acquire()
    self.lib.reset(1)
    self.assertFalse(self.call('authorize',p,t))

if __name__=='__main__':
  unittest.main(verbosity=2)
