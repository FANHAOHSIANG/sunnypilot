"""Bench-only source-matched 0x160 relay; no Params, device or network access.

The safety side independently verifies origin, CRC, sequence, age, and exact
payload equality. This controller helper never makes up FCA availability.
"""
from __future__ import annotations

ADDRESS = 0x160
SOURCE_BUS = 2
DESTINATION_BUS = 0
VEHICLE_BENCH_FLAG = 1 << 28
SAFETY_BENCH_FLAG = 2048
SUPPORTED_BASE_FLAGS = 134227976
SUPPORTED_BASE_PARAM = 1037
MAX_AGE_NS = 100_000_000


def checksum(data: bytes) -> int:
  if len(data) != 16:
    raise ValueError("0x160 must be exactly 16 bytes")
  crc = 0
  for value in data[2:] + bytes((ADDRESS & 255, ADDRESS >> 8)):
    crc ^= value << 8
    for _ in range(8):
      crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
  return crc ^ 0x041D


def valid_payload(data: bytes) -> bool:
  return len(data) == 16 and int.from_bytes(data[:2], "little") == checksum(data)


def validate_bench_cp(cp) -> None:
  """Reject a partial or mismatched controller/safety configuration."""
  if not int(cp.flags) & VEHICLE_BENCH_FLAG:
    return
  configs = list(cp.safetyConfigs)
  if (str(cp.carFingerprint) != "HYUNDAI_IONIQ_5" or not cp.openpilotLongitudinalControl or
      int(cp.flags) != SUPPORTED_BASE_FLAGS | VEHICLE_BENCH_FLAG or len(configs) != 1 or
      str(configs[0].safetyModel) != "hyundaiCanfd" or
      int(configs[0].safetyParam) != SUPPORTED_BASE_PARAM | SAFETY_BENCH_FLAG):
    raise ValueError("FCW bench candidate requires exact paired Ioniq 5 controller/safety configuration")


class FcaProxy:
  def __init__(self):
    self.last_source_ns = -1

  def forward(self, data: bytes | None, source_ns: int, now_ns: int, source_bus: int, destination_bus: int):
    """Return an original CAN tuple once; never pack or modify its fields."""
    if (data is None or source_bus != SOURCE_BUS or destination_bus != DESTINATION_BUS or
        source_ns <= self.last_source_ns or not 0 <= now_ns - source_ns <= MAX_AGE_NS or
        not valid_payload(data)):
      return None
    self.last_source_ns = source_ns
    return ADDRESS, bytes(data), DESTINATION_BUS
