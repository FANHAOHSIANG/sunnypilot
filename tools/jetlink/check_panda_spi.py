#!/usr/bin/env python3
"""Check the pinned HKG SPI wire interface and compile its real host source.

Common utility/logging functions are stubbed for syntax checking only.
This does not test physical SPI, timings, full pandad linking, or C4 boot.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def number(source, name):
  match = re.search(r'^\s*#define\s+' + re.escape(name) + r'\s+(0x[0-9a-fA-F]+|[0-9]+)[Uu]?\b', source, re.MULTILINE)
  if match is None:
    raise ValueError(f'Missing SPI constant {name}; review protocol migration')
  return int(match.group(1), 0)

def check_wire(host, firmware, comms, drivers):
  for local, remote in [('SPI_SYNC', 'SPI_SYNC_BYTE'), ('SPI_HACK', 'SPI_HACK'),
                        ('SPI_DACK', 'SPI_DACK'), ('SPI_NACK', 'SPI_NACK'),
                        ('SPI_CHECKSUM_START', 'SPI_CHECKSUM_START')]:
    if number(host, local) != number(firmware, remote):
      raise ValueError(f'SPI protocol mismatch: {local}/{remote}')
  fields = re.search(r'struct __attribute__\(\(packed\)\) spi_header \{([^}]+)\}', comms)
  if fields is None or re.findall(r'uint(?:8|16|32)_t\s+(\w+);', fields.group(1)) != ['sync', 'endpoint', 'tx_len', 'max_rx_len']:
    raise ValueError('SPI request header differs from pinned legacy firmware')
  if number(firmware, 'SPI_HEADER_SIZE') != 7:
    raise ValueError('Unexpected firmware SPI header length')
  if not 64 < number(comms, 'SPI_BUF_SIZE') <= number(drivers, 'SPI_BUF_SIZE'):
    raise ValueError('Host SPI buffer exceeds firmware capacity')
  if 'add_checksum(tx_buf, sizeof(header))' not in host or 'SPI_CRC_SIZE' in host:
    raise ValueError('SPI checksum framing differs from pinned firmware')

def main():
  host = (ROOT / 'openpilot/selfdrive/pandad/spi.cc').read_text()
  firmware = (ROOT / 'panda/board/drivers/spi.h').read_text()
  comms = (ROOT / 'openpilot/selfdrive/pandad/panda_comms.h').read_text()
  drivers = (ROOT / 'panda/board/drivers/drivers.h').read_text()
  check_wire(host, firmware, comms, drivers)
  try:
    check_wire(host.replace('#define SPI_SYNC 0x5AU', '#define SPI_SYNC 0x3CU'),
               firmware, comms, drivers)
  except ValueError:
    pass
  else:
    raise AssertionError('A mismatched SPI sync must fail')
  with tempfile.TemporaryDirectory(prefix='panda-spi-') as directory:
    root = Path(directory)
    (root / 'common').mkdir()
    (root / 'common/util.h').write_text("""
#pragma once
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <fcntl.h>
#include <unistd.h>
#include <thread>
#include <stdexcept>
#include <string>
namespace util {
bool file_exists(const std::string &);
int safe_ioctl(int, unsigned long, void *, const char * = "");
std::string hexdump(const void *, int);
std::string getenv(const char *, const char *);
}
""")
    (root / 'common/timing.h').write_text('#include <cstdint>\nuint64_t nanos_since_boot();\ndouble millis_since_boot();\n')
    (root / 'common/swaglog.h').write_text('#define LOGE(...) ((void)0)\n#define LOGD(...) ((void)0)\n#define LOGW(...) ((void)0)\n')
    subprocess.run(['c++', '-std=c++17', '-fsyntax-only',
                    '-I', str(root), '-I', str(ROOT), '-I', str(ROOT / 'openpilot'),
                    str(ROOT / 'openpilot/selfdrive/pandad/spi.cc')], check=True)
    # Compile/run the actual host checksum routines and verify the complete
    # header layout and every possible single-byte payload against firmware XOR.
    start = host.index('void add_checksum(')
    end = host.index('int PandaSpiHandle::spi_transfer_retry(', start)
    functions = host[start:end]
    code = """
#include <cstdint>
#include <cassert>
#include <cstddef>
#include <atomic>
#include <mutex>
#include <string>
#include <vector>
#define private public
#include "selfdrive/pandad/panda_comms.h"
#undef private
#define SPI_CHECKSUM_START 0xABU
""" + functions + """
int main() {
  static_assert(sizeof(PandaSpiHandle::spi_header) == 6);
  static_assert(offsetof(PandaSpiHandle::spi_header, endpoint) == 1);
  static_assert(offsetof(PandaSpiHandle::spi_header, tx_len) == 2);
  static_assert(offsetof(PandaSpiHandle::spi_header, max_rx_len) == 4);
  for (unsigned value = 0; value < 256; ++value) {
    uint8_t bytes[] = {(uint8_t)value, 0};
    add_checksum(bytes, 1);
    assert((uint8_t)(0xABU ^ bytes[0] ^ bytes[1]) == 0);
    assert(check_checksum(bytes, 2));
    bytes[1] ^= 1;
    assert(!check_checksum(bytes, 2));
  }
}
"""
    (root / 'checksum.cc').write_text(code)
    subprocess.run(['c++', '-std=c++17', '-I', str(ROOT / 'openpilot'),
                    str(root / 'checksum.cc'), '-o', str(root / 'checksum')], check=True)
    subprocess.run([str(root / 'checksum')], check=True)
  print('PASS: HKG SPI wire constants, header, checksums, mismatch rejection, host syntax')
  print('Physical SPI, full pandad/C4 build and boot remain unverified.')

if __name__ == '__main__':
  main()
