"""Compare the pinned HCOP interface with a parked.py summary, without a Panda or Params."""
import argparse
import json
from pathlib import Path

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import HyundaiFlags


def evaluate(summary):
  recorded = summary['car']
  if recorded['name'] != 'HYUNDAI_IONIQ_5' or not summary['samples']['can_events']:
    raise ValueError('need a populated IONIQ 5 capture')
  fp = gen_empty_fingerprint()
  for bus, frames in summary['observed_ids'].items():
    for address, dlc in frames:
      fp[int(bus)][int(address, 16)] = dlc
  # get_params uses the presence of Ecu.adas, not its private firmware string.
  # Do not create synthetic firmware entries when the capture has none.
  fw = [structs.CarParams.CarFw(ecu=ecu) for ecu in recorded['ecu_types']]
  result = []
  for alpha in (False, True):
    cp = CarInterface.get_params(recorded['name'], fp, fw, alpha, False, False)
    bus = CanBus(cp)
    result.append(dict(requested_alpha=alpha, observed_firmware=recorded['firmware_count'],
                       flags=[flag.name for flag in HyundaiFlags if cp.flags & flag],
                       longitudinal=cp.openpilotLongitudinalControl, radar_unavailable=cp.radarUnavailable,
                       acan=bus.ACAN, ecan=bus.ECAN, cam=bus.CAM,
                       safety=[(str(x.safetyModel), x.safetyParam) for x in cp.safetyConfigs]))
  return result


if __name__ == '__main__':
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument('summary', type=Path)
  args = ap.parse_args()
  print(json.dumps(evaluate(json.loads(args.summary.read_text())), indent=2))
