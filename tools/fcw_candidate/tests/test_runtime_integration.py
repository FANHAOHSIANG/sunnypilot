"""Actual patched parser/controller scheduling tests, offline only.

Uses the real CANParser, CANPacker, CarController method, and FcaProxy.
Unrelated steering/acceleration generation is stubbed; this is not a whole-car
simulation and must run alongside the preserved full safety suite.
"""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from opendbc.can import CANParser, CANPacker
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai import hyundaicanfd
from opendbc.car.hyundai.fca_proxy import FcaProxy, VEHICLE_BENCH_FLAG

class RuntimeIntegration(unittest.TestCase):
    def setUp(self):
        self.packer=CANPacker('hyundai_canfd_generated')
        self.parser=CANParser('hyundai_canfd_generated',[('ADRV_0x160',50)],2)
        self.t=2_000_000_000
    def source(self,setting=3):
        return self.packer.make_can_msg('ADRV_0x160',2,{'AEB_SETTING':setting})
    def controller(self,enabled=True):
        c=CarController.__new__(CarController)
        c.CP=NS(flags=134227976 | (VEHICLE_BENCH_FLAG if enabled else 0),openpilotLongitudinalControl=True)
        c.CAN=NS(CAM=2,ECAN=0,ACAN=1)
        c.frame=0;c.lkas_icon=0;c.lfa_icon=0;c.packer=self.packer
        c.fca_proxy=FcaProxy();c.fca_now_ns=self.t;c.accel_last=0.0
        c.lead_data=None;c.tuning=None
        return c
    def schedule(self,c,data,source_ns):
        cs=NS(fca_source_data=data,fca_source_ns=source_ns,main_cruise_enabled=False)
        cc=NS(enabled=False,cruiseControl=NS(override=False))
        with patch.object(hyundaicanfd,'create_steering_messages',return_value=[]), \
             patch.object(hyundaicanfd,'create_lfahda_cluster',return_value=[]), \
             patch.object(hyundaicanfd,'create_acc_control',return_value=(0x1a0,bytes(32),0)):
            return c.create_canfd_msgs(False,0,30.0,0.0,False,NS(leadDistanceBars=2),cs,cc)
    def test_parser_retains_exact_valid_source(self):
        frame=self.source();self.parser.update([(self.t,[frame])])
        state=self.parser.message_states[0x160]
        self.assertEqual(state.last_valid_raw,frame[1])
        self.assertEqual(state.last_valid_raw_nanos,self.t)
    def test_parser_crc_failure_does_not_replace_snapshot(self):
        frame=self.source();self.parser.update([(self.t,[frame])])
        bad=bytearray(frame[1]);bad[0]^=1
        self.parser.update([(self.t+20_000_000,[(0x160,bytes(bad),2)])])
        state=self.parser.message_states[0x160]
        self.assertEqual(state.last_valid_raw,frame[1])
        self.assertEqual(state.last_valid_raw_nanos,self.t)
    def test_wrong_bus_cannot_supply_source(self):
        frame=self.source();self.parser.update([(self.t,[(0x160,frame[1],0)])])
        self.assertEqual(self.parser.message_states[0x160].last_valid_raw,b'')
    def test_actual_controller_schedules_exact_original_once(self):
        frame=self.source();self.parser.update([(self.t,[frame])])
        state=self.parser.message_states[0x160];c=self.controller()
        messages=self.schedule(c,state.last_valid_raw,state.last_valid_raw_nanos)
        originals=[m for m in messages if m[0]==0x160]
        self.assertEqual(originals,[(0x160,frame[1],0)])
        self.assertFalse(any(m[0]==0x160 for m in self.schedule(c,frame[1],self.t)))
    def test_missing_source_does_not_fabricate_disabled_status(self):
        self.assertFalse(any(m[0]==0x160 for m in self.schedule(self.controller(),None,0)))
    def test_expired_source_is_not_scheduled(self):
        frame=self.source()
        self.assertFalse(any(m[0]==0x160 for m in self.schedule(self.controller(),frame[1],self.t-100_000_001)))
    def test_feature_off_preserves_existing_warning_path(self):
        messages=self.schedule(self.controller(False),None,0)
        warnings=[m for m in messages if m[0]==0x160]
        self.assertEqual(len(warnings),1)
        self.assertEqual(warnings[0][1][3]&3,1)
    def test_original_off_status_is_not_forged_active(self):
        frame=self.source(1)
        originals=[m for m in self.schedule(self.controller(),frame[1],self.t) if m[0]==0x160]
        self.assertEqual(originals,[(0x160,frame[1],0)])
        self.assertEqual(originals[0][1][3]&3,1)

if __name__=='__main__':unittest.main(verbosity=2)
