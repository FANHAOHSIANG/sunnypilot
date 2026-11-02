#!/usr/bin/env python3
"""Synthetic DBC parser regression. No car or CAN interface."""
import tempfile
import unittest
from pathlib import Path
from compare_canfd_dbc import parse_dbc, compare

class DbcComparisonTests(unittest.TestCase):
    def test_bit_layout_difference_and_missing_signal(self):
        left = """VERSION ""
BO_ 352 ADRV_0x160: 16 ADRV
 SG_ AEB_SETTING : 24|2@1+ (1,0) [0|3] "" XXX
BO_ 373 TCS: 24 XXX
 SG_ DriverBraking : 81|1@0+ (1,0) [0|1] "" XXX
BO_ 416 SCC_CONTROL: 32 ADRV
 SG_ StopReq : 185|2@0+ (1,0) [0|3] "" XXX
"""
        right = """VERSION ""
BO_ 352 ADRV_0x160: 16 ADRV
 SG_ AEB_SETTING : 24|2@1+ (1,0) [0|3] "" XXX
BO_ 373 TCS: 24 XXX
 SG_ DriverBraking : 81|1@0+ (1,0) [0|1] "" XXX
BO_ 416 SCC_CONTROL: 32 ADRV
 SG_ StopReq : 184|1@0+ (1,0) [0|1] "" XXX
 SG_ NEW_SIGNAL : 192|1@1+ (1,0) [0|1] "" XXX
"""
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.dbc", Path(tmp) / "b.dbc"
            a.write_text(left)
            b.write_text(right)
            diff = compare(parse_dbc(a), parse_dbc(b))
        scc = next(m for m in diff if m["name"] == "SCC_CONTROL")
        self.assertEqual(len(scc["different_signals"]), 2)
        self.assertEqual(next(x for x in scc["different_signals"] if x["signal"] == "StopReq")["carrot"].split()[0], "185|2@0+")
        self.assertIsNone(next(x for x in scc["different_signals"] if x["signal"] == "NEW_SIGNAL")["carrot"])
        self.assertEqual(next(m for m in diff if m["name"] == "ADRV_0x160")["different_signals"], [])

if __name__ == "__main__":
    unittest.main()
