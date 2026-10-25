#!/usr/bin/env python3
"""Synthetic offline tests for fca_scc_timeline; no car access or CAN TX."""
import unittest
from types import SimpleNamespace as NS
from fca_scc_timeline import extract_segment

class FakeEvent:
    def __init__(self, kind, ns, **data):
        self.kind = kind
        self.logMonoTime = ns
        for key, value in data.items():
            setattr(self, key, value)
    def which(self):
        return self.kind

class PassiveTimelineTests(unittest.TestCase):
    def test_rx_tx_separated_and_diagnostics_detected(self):
        frame = lambda addr, bus, dat: NS(address=addr, src=bus, dat=dat)
        events = [
            FakeEvent("can", 1000000000, can=[frame(0x160, 2, b"\x00" * 16)]),
            FakeEvent("sendcan", 1200000000, sendcan=[
                frame(0x160, 0, b"\x01" * 16),
                frame(0x1A0, 0, b"\x02" * 32),
                frame(0x7D0, 0, b"\x03\x28\x83\x01\x00\x00\x00\x00"),
                frame(0x730, 1, b"\x02\x3e\x80\x00\x00\x00\x00\x00"),
            ]),
            FakeEvent("carState", 2200000000, carState=NS(stockFcw=True, stockAeb=False)),
        ]
        out = extract_segment(events, "synthetic")
        counts = {(r["direction"], r["message"], r["bus"]): r["count"] for r in out["counts"]}
        self.assertEqual(counts[("can", "ADRV_0x160", 2)], 1)
        self.assertEqual(counts[("sendcan", "ADRV_0x160", 0)], 1)
        self.assertEqual(counts[("sendcan", "SCC_CONTROL", 0)], 1)
        self.assertEqual(out["diagnostic_requests"]["disable_communication_request"], 1)
        self.assertEqual(out["diagnostic_requests"]["tester_present_suppress_response"], 1)
        self.assertEqual(out["safety_event_samples"]["stockFcw_sample"], 1)
        self.assertNotIn("stockAeb_sample", out["safety_event_samples"])

if __name__ == "__main__":
    unittest.main()
