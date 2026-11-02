#!/usr/bin/env python3
"""Exact Ioniq 5 CAN FD safetyParam=1037 regression, offline only.

Uses the pinned opendbc C safety hooks. Never opens a Panda device,
never sends CAN on a physical bus, and never alters vehicle settings.
"""
from __future__ import annotations

import unittest

from opendbc.car.structs import CarParams
from opendbc.car.hyundai.values import HyundaiSafetyFlags
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


PARAM = 1037  # EV_GAS | LONG | CAMERA_SCC | CANFD_DYNAMIC_TORQUE


class TestIoniq5Safety1037(unittest.TestCase):
    def setUp(self):
        self.safety = libsafety_py.libsafety
        self.packer = CANPackerSafety("hyundai_canfd_generated")
        ret = self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, PARAM)
        self.assertEqual(ret, 0, "Unable to initialize Hyundai CAN FD safety hooks")
        self.safety.init_tests()

    def test_exact_flag_configuration(self):
        expected = int(HyundaiSafetyFlags.EV_GAS | HyundaiSafetyFlags.LONG |
                       HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CANFD_DYNAMIC_TORQUE)
        self.assertEqual(expected, PARAM)
        self.assertEqual(self.safety.get_current_safety_param(), PARAM)
        self.assertEqual(self.safety.get_current_safety_mode(), int(CarParams.SafetyModel.hyundaiCanfd))

    def test_out_of_bounds_longitudinal_requests_rejected(self):
        # These commands are intentionally invalid. Confirm the safety hook rejects
        # them even if the controller were to request engagement.
        self.safety.set_controls_allowed(True)
        for accel in (-9.0, 9.0):
            with self.subTest(accel=accel):
                msg = self.packer.make_can_msg_safety(
                    "SCC_CONTROL", 0, {"aReqRaw": accel, "aReqValue": accel})
                self.assertFalse(self.safety.safety_tx_hook(msg),
                                 "Safety allowed an out-of-range longitudinal request")

    def test_tcs_transmission_not_whitelisted(self):
        # Do not grant openpilot permission to forge OEM brake-state messages.
        msg = self.packer.make_can_msg_safety("TCS", 0, {"DriverBraking": 0})
        self.assertFalse(self.safety.safety_tx_hook(msg))

    def test_adas_uds_on_wrong_bus_rejected(self):
        # Safety should not allow arbitrary diagnostic messages on unrelated buses.
        msg = libsafety_py.make_CANPacket(0x730, 0, b"\x02\x3e\x80\x00\x00\x00\x00\x00")
        self.assertFalse(self.safety.safety_tx_hook(msg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
