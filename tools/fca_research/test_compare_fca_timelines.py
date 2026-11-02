#!/usr/bin/env python3
"""Synthetic, offline tests of carrot-vs-sunnypilot timeline comparison."""
import unittest
from compare_fca_timelines import compare


def report(tx, rx, fcw=0):
    return {
        "type": "passive_fca_scc_timeline",
        "segments": [{
            "counts": [
                {"direction": "sendcan", "message": "ADRV_0x160", "bus": 0, "count": tx},
                {"direction": "can", "message": "ADRV_0x160", "bus": 2, "count": rx},
            ],
            "seconds_with_activity": [{"second": 0}, {"second": 9}],
            "safety_event_samples": {"stockFcw_sample": fcw},
            "diagnostic_requests": {},
        }],
    }


class ComparisonTests(unittest.TestCase):
    def test_direction_and_bus_are_not_merged(self):
        result = compare(report(10, 20, fcw=1), report(30, 40))
        rows = {(r["direction"], r["bus"]): r for r in result["can_comparison"]}
        self.assertEqual(rows[("sendcan", 0)]["carrot_frames"], 10)
        self.assertEqual(rows[("can", 2)]["sunnypilot_frames"], 40)
        self.assertEqual(rows[("sendcan", 0)]["carrot_frames_per_observed_second"], 1)
        self.assertEqual(result["recorded_stock_event_samples"]["carrot"]["stockFcw_sample"], 1)
        self.assertIn("NOT AEB-VERIFIED", result["conclusion"])


if __name__ == "__main__":
    unittest.main()
