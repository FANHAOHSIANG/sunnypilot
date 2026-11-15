"""Full patched libsafety integration. Run only in an isolated test tree.
FCW_EXPECT_BENCH=1 requires HYUNDAI_FCW_BENCH in temporary safety.c only.
No Panda device access or firmware flashing. These tests are pending until run.
"""
import os
import unittest
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py
BASE=1037
CANDIDATE=BASE|2048

class Fixture(unittest.TestCase):
  def setUp(self):
    self.safety=libsafety_py.libsafety
    self.safety.set_current_safety_param_sp(0)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd,CANDIDATE),0)
    self.safety.init_tests()
    self.packer=CANPackerSafety('hyundai_canfd_generated')
    self.time=100000
  def source(self,count,setting=3):
    self.safety.set_timer(self.time)
    return self.packer.make_can_msg_safety('ADRV_0x160',2,{'COUNTER':count,'AEB_SETTING':setting})
  def acquire(self,setting=3):
    for counter in range(3):
      m=self.source(counter,setting)
      self.assertTrue(self.safety.safety_rx_hook(m))
      self.last_data=bytes(m[0].data[:16])
      self.time+=20000
    self.safety.set_timer(self.time)
  def tx_copy(self,data=None):
    return libsafety_py.make_CANPacket(0x160,0,self.last_data if data is None else data)
  def accel(self,a):
    return self.packer.make_can_msg_safety('SCC_CONTROL',0,{'aReqRaw':a,'aReqValue':a})

class ProductionLock(Fixture):
  def test_candidate_param_has_no_transmit_on_production_build(self):
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x160,0,bytes(16))))
  def test_candidate_param_has_no_forwarding_on_production_build(self):
    for address in (0x160,0x161,0x175,0x1a0,0x12a):
      self.assertEqual(self.safety.safety_fwd_hook(2,address),-1)
  def test_baseline_param_keeps_baseline_behavior(self):
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd,BASE)
    self.safety.init_tests()
    self.assertEqual(self.safety.safety_fwd_hook(2,0x160),-1)
    self.assertEqual(self.safety.safety_fwd_hook(2,0x161),0)

class BenchIntegration(Fixture):
  def test_matching_original_once(self):
    self.acquire()
    self.assertTrue(self.safety.safety_tx_hook(self.tx_copy()))
    self.assertFalse(self.safety.safety_tx_hook(self.tx_copy()))
  def test_fabricated_off_message_rejected(self):
    self.acquire()
    off=self.packer.make_can_msg_safety('ADRV_0x160',0,{'AEB_SETTING':1})
    self.assertFalse(self.safety.safety_tx_hook(off))
    self.assertTrue(self.safety.safety_tx_hook(self.tx_copy()))
  def test_unknown_bits_cannot_be_modified(self):
    self.acquire()
    data=bytearray(self.last_data);data[13]^=1
    self.assertFalse(self.safety.safety_tx_hook(self.tx_copy(bytes(data))))
  def test_without_source_rejects_host_and_active_scc(self):
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x160,0,bytes(16))))
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
  def test_freshness_timeout_rejects_host_and_active_scc(self):
    self.acquire();self.safety.set_timer(self.time+100001)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.tx_copy()))
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
    self.assertFalse(self.safety.get_controls_allowed())
  def test_original_off_status_preserved_but_longitudinal_denied(self):
    self.acquire(setting=1)
    self.assertTrue(self.safety.safety_tx_hook(self.tx_copy()))
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
  def test_raw_forwarding_still_isolated(self):
    self.acquire()
    for address in (0x160,0x1a0,0x12a,0x1e0):
      self.assertEqual(self.safety.safety_fwd_hook(2,address),-1)
    self.assertEqual(self.safety.safety_fwd_hook(2,0x161),0)
  def test_tcs_never_new_host_transmit(self):
    self.acquire()
    for bus in (0,1,2):
      m=self.packer.make_can_msg_safety('TCS',bus,{'DriverBraking':0})
      self.assertFalse(self.safety.safety_tx_hook(m))
  def test_acceleration_bounds_unchanged(self):
    self.acquire()
    self.assertTrue(self.safety.safety_tx_hook(self.tx_copy()))
    for a,expected in ((-3.51,False),(-3.5,True),(0.,True),(2.,True),(2.01,False)):
      self.safety.set_controls_allowed(True)
      self.assertEqual(bool(self.safety.safety_tx_hook(self.accel(a))),expected,a)
  def test_driver_brake_cancels(self):
    self.acquire();self.safety.set_controls_allowed(True)
    brake=self.packer.make_can_msg_safety('TCS',0,{'DriverBraking':1})
    self.assertTrue(self.safety.safety_rx_hook(brake))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
  def test_cancel_button_cancels(self):
    self.acquire();self.safety.set_controls_allowed(True)
    cancel=self.packer.make_can_msg_safety('CRUISE_BUTTONS',0,{'CRUISE_BUTTONS':4})
    self.assertTrue(self.safety.safety_rx_hook(cancel))
    self.assertFalse(self.safety.get_controls_allowed())
  def test_relay_fault_blocks_even_source_match(self):
    self.acquire();self.safety.set_relay_malfunction(True)
    self.assertFalse(self.safety.safety_tx_hook(self.tx_copy()))
    self.assertEqual(self.safety.safety_fwd_hook(2,0x161),-1)
  def test_unexpected_stock_scc_still_faults(self):
    self.acquire()
    stock=self.packer.make_can_msg_safety('SCC_CONTROL',0,{'aReqRaw':0.,'aReqValue':0.})
    self.safety.safety_rx_hook(stock)
    self.assertTrue(self.safety.get_relay_malfunction())
  def test_unexpected_0x160_on_destination_still_faults(self):
    self.acquire()
    self.safety.safety_rx_hook(self.tx_copy())
    self.assertTrue(self.safety.get_relay_malfunction())
  def test_corrupt_crc_disengages(self):
    self.acquire();self.safety.set_controls_allowed(True)
    m=self.source(3);m[0].data[0]^=1
    self.assertFalse(self.safety.safety_rx_hook(m))
    self.assertFalse(self.safety.get_controls_allowed())
  def test_mismatched_feature_parameter_denied(self):
    for base in (13,1025,1045):
      self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd,base|2048)
      self.safety.init_tests();self.safety.set_controls_allowed(True)
      self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
      self.assertEqual(self.safety.safety_fwd_hook(2,0x161),-1)
  def test_receive_only_cannot_enable_longitudinal(self):
    self.acquire()
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
    self.assertFalse(self.safety.get_controls_allowed())
  def test_proxy_stall_with_live_source_cancels(self):
    self.acquire()
    self.assertTrue(self.safety.safety_tx_hook(self.tx_copy()))
    for counter in range(3,10):
      m=self.source(counter)
      self.assertTrue(self.safety.safety_rx_hook(m))
      self.time+=20000
    self.safety.set_timer(self.time)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.accel(1.0)))
    self.assertFalse(self.safety.get_controls_allowed())
  def test_no_automatic_engagement_after_source_recovery(self):
    self.acquire()
    self.assertFalse(self.safety.get_controls_allowed())

if __name__=='__main__':
  cls=BenchIntegration if os.environ.get('FCW_EXPECT_BENCH')=='1' else ProductionLock
  result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
  raise SystemExit(0 if result.wasSuccessful() else 1)
