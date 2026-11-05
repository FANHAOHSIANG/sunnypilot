#!/usr/bin/env python3
"""Synthetic regression for offline FCA provenance report."""
import unittest
from types import SimpleNamespace as NS
from vehicle_fca_provenance import summarize

class Event:
    def __init__(self, kind, **data):
        self.kind = kind
        for k, v in data.items():
            setattr(self, k, v)
    def which(self):
        return self.kind

def frame(addr, bus, data):
    return NS(address=addr, src=bus, dat=data)

class ProvenanceTests(unittest.TestCase):
    def test_vehicle_params_and_raw_signals(self):
        cp = NS(carFingerprint="HYUNDAI_IONIQ_5", flags=0, openpilotLongitudinalControl=True,
                safetyConfigs=[NS(safetyModel="hyundaiCanfd", safetyParam=1037)])
        events = [
            Event("carParams", carParams=cp),
            Event("can", can=[frame(0x160, 2, bytes([0,0,0,3]+[0]*12)),
                              frame(0x175, 0, bytes([0]*12+[1]+[0]*11))]),
            Event("sendcan", sendcan=[frame(0x160, 0, bytes([0,0,0,1]+[0]*12))]),
            Event("carState", carState=NS(stockFcw=True, stockAeb=False)),
            Event("selfdriveState", selfdriveState=NS(alertType="fcw")),
        ]
        result = summarize(events, "synthetic")
        self.assertTrue(result["carParams_present"])
        self.assertTrue(result["openpilotLongitudinalControl"])
        self.assertEqual(result["safetyConfigs"][0]["param"], 1037)
        signals = {(x["direction"], x["bus"], x["signal"], x["raw_value"]):x["count"] for x in result["canSignals"]}
        self.assertEqual(signals[("can",2,"AEB_SETTING",3)],1)
        self.assertEqual(signals[("sendcan",0,"AEB_SETTING",1)],1)
        self.assertEqual(signals[("can",0,"FCA_EquipSta",1)],1)
        self.assertEqual(signals[("can",0,"FCA_AvlblSta",0)],1)
        self.assertEqual(result["stockWarningSamples"]["stockFcw"],1)

    def test_missing_carparams_is_unknown(self):
        result = summarize([], "empty")
        self.assertFalse(result["carParams_present"])
        self.assertIsNone(result["vehicleFlagsRaw"])
        self.assertEqual(result["vehicleFlagNames"], [])

if __name__ == "__main__":
    unittest.main(verbosity=2)
