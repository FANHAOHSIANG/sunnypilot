"""Regression for both directions of controller/Panda configuration mismatch."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'candidate_src'))
from fca_proxy import validate_bench_cp, VEHICLE_BENCH_FLAG, SAFETY_BENCH_FLAG


def config(vehicle_on=False, safety_on=False):
    return NS(carFingerprint='HYUNDAI_IONIQ_5',
              flags=134227976 | (VEHICLE_BENCH_FLAG if vehicle_on else 0),
              openpilotLongitudinalControl=True,
              safetyConfigs=[NS(safetyModel='hyundaiCanfd',
                                safetyParam=1037 | (SAFETY_BENCH_FLAG if safety_on else 0))])


class PairingRegression(unittest.TestCase):
    def test_safety_only_activation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'paired|mismatch'):
            validate_bench_cp(config(False, True))

    def test_controller_only_activation_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_bench_cp(config(True, False))

    def test_both_off_keeps_baseline(self):
        validate_bench_cp(config(False, False))

    def test_both_on_supported_pair_is_accepted(self):
        validate_bench_cp(config(True, True))

    def test_unknown_other_brand_bit_does_not_change_baseline(self):
        cp=config(False, True)
        cp.carFingerprint='OTHER_BRAND'
        cp.safetyConfigs[0].safetyModel='toyota'
        validate_bench_cp(cp)

    def test_duplicate_hyundai_config_is_rejected(self):
        cp=config(True, True)
        cp.safetyConfigs.append(cp.safetyConfigs[0])
        with self.assertRaises(ValueError):
            validate_bench_cp(cp)

if __name__ == '__main__':
    unittest.main(verbosity=2)
