import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'candidate_src'))
from fca_proxy import FcaProxy, checksum, valid_payload, validate_bench_cp, VEHICLE_BENCH_FLAG


def packet(c=1,setting=3):
 p=bytearray.fromhex('0000000300000002fffc010000000000')
 p[2]=c;p[3]=setting;p[:2]=checksum(bytes(p)).to_bytes(2,'little')
 return bytes(p)


def cp():
 return NS(carFingerprint='HYUNDAI_IONIQ_5', flags=134227976|VEHICLE_BENCH_FLAG,
           openpilotLongitudinalControl=True,safetyConfigs=[NS(safetyModel='hyundaiCanfd',safetyParam=3085)])


class TestControllerProxy(unittest.TestCase):
 def test_exact_supported_config(self): validate_bench_cp(cp())
 def test_baseline_remains_default(self):
  c=cp();c.flags=134227976;c.safetyConfigs[0].safetyParam=1037;validate_bench_cp(c)
 def test_reject_mismatched_safety(self):
  c=cp();c.safetyConfigs[0].safetyParam=1037
  with self.assertRaises(ValueError):validate_bench_cp(c)
 def test_reject_unverified_car(self):
  c=cp();c.carFingerprint='HYUNDAI_IONIQ_5_PE'
  with self.assertRaises(ValueError):validate_bench_cp(c)
 def test_reject_lka_variant(self):
  c=cp();c.flags|=1
  with self.assertRaises(ValueError):validate_bench_cp(c)
 def test_reject_no_long(self):
  c=cp();c.openpilotLongitudinalControl=False
  with self.assertRaises(ValueError):validate_bench_cp(c)
 def test_payload_is_bit_identical(self):
  d=packet();p=FcaProxy();m=p.forward(d,1000000000,1010000000,2,0)
  self.assertEqual(m,(0x160,d,0))
 def test_duplicate_not_sent(self):
  d=packet();p=FcaProxy();self.assertIsNotNone(p.forward(d,1000,2000,2,0))
  self.assertIsNone(p.forward(d,1000,3000,2,0))
 def test_old_data_not_sent(self):
  self.assertIsNone(FcaProxy().forward(packet(),1000,100001001,2,0))
 def test_future_data_not_sent(self):
  self.assertIsNone(FcaProxy().forward(packet(),1001,1000,2,0))
 def test_wrong_buses_not_sent(self):
  for source,destination in ((0,0),(1,0),(2,1),(2,2),(128,0)):
   self.assertIsNone(FcaProxy().forward(packet(),1000,2000,source,destination))
 def test_bad_crc_not_sent(self):
  p=bytearray(packet());p[7]^=1
  self.assertIsNone(FcaProxy().forward(bytes(p),1000,2000,2,0))
 def test_bad_lengths_not_sent(self):
  for n in (0,1,15,17,32):self.assertIsNone(FcaProxy().forward(bytes(n),1000,2000,2,0))
 def test_all_enum_values_preserved(self):
  for v in range(4):
   d=packet(setting=v);self.assertEqual(FcaProxy().forward(d,1000,2000,2,0)[1],d)
 def test_no_source_has_no_replacement(self):
  self.assertIsNone(FcaProxy().forward(None,0,1000,2,0))

if __name__=='__main__': unittest.main(verbosity=2)
