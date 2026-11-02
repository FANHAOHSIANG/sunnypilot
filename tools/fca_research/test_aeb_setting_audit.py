#!/usr/bin/env python3
"""Synthetic tests: no openpilot runtime, Panda, or vehicle required."""
import unittest
from types import SimpleNamespace as NS
from aeb_setting_audit import decode_aeb_setting, summarize

class Event:
    def __init__(self, name, t, frames):
        self._name = name
        self.logMonoTime = t
        setattr(self, name, frames)
    def which(self):
        return self._name

def msg(addr, bus, data):
    return NS(address=addr, src=bus, dat=data)

class TestAebSettingAudit(unittest.TestCase):
    def test_bitfield_matches_captured_examples(self):
        # User's previously recorded TX and bus-2 RX sample frames.
        self.assertEqual(decode_aeb_setting(bytes.fromhex("2d84000100000002fffc090000000000")), 1)
        self.assertEqual(decode_aeb_setting(bytes.fromhex("41bab00300000002fffc010000000000")), 3)

    def test_length_rejected(self):
        with self.assertRaises(ValueError):
            decode_aeb_setting(b"\x01")

    def test_tx_and_rx_not_merged(self):
        events = [
            Event("sendcan", 1000000000, [msg(0x160, 0, bytes([0,0,0,1]+[0]*12)),
                                          msg(0x1A0, 0, bytes(32))]),
            Event("can", 1100000000, [msg(0x160, 2, bytes([0,0,0,3]+[0]*12))]),
        ]
        report = summarize(events, "synthetic")
        by_key = {(x["direction"],x["bus"],x["raw_value"]):x["frames"] for x in report["aeb_setting"]}
        self.assertEqual(by_key[("sendcan",0,1)], 1)
        self.assertEqual(by_key[("can",2,3)], 1)
        self.assertEqual(report["scc_control"][0]["frames"], 1)

if __name__ == "__main__":
    unittest.main(verbosity=2)
