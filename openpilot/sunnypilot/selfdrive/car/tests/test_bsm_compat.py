import unittest

from opendbc.car.structs import CarParams
from openpilot.sunnypilot.selfdrive.car.bsm_compat import has_bsm


class HkgBsmCompatibilityTests(unittest.TestCase):
  def test_detected_blind_spot_equipment_stays_available(self):
    cp = CarParams()
    cp.brand = 'hyundai'
    cp.enableBsm = True
    self.assertTrue(has_bsm(cp, None))

  def test_car_without_blind_spot_equipment_stays_unavailable(self):
    cp = CarParams()
    cp.brand = 'hyundai'
    cp.enableBsm = False
    self.assertFalse(has_bsm(cp, None))
