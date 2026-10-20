#!/usr/bin/env python3
"""Check the Panda/opendbc safety_tick calling interface, not vehicle safety behavior."""
from pathlib import Path
import subprocess
import tempfile
import re
import sys

ROOT = Path(__file__).resolve().parents[2]

def arity(arguments):
  arguments = arguments.strip()
  return 0 if arguments in ('', 'void') else len(arguments.split(','))

def check(header, main):
  declaration = re.search(r'\bvoid\s+safety_tick\s*\(([^)]*)\)', header)
  calls = re.findall(r'\bsafety_tick\s*\(([^()]*)\)\s*;', main)
  if declaration is None or not calls:
    raise ValueError('Cannot identify safety_tick interface; review these dependency versions')
  expected = arity(declaration.group(1))
  for arguments in calls:
    actual = arity(arguments)
    if actual != expected:
      raise ValueError(f'Panda/opendbc mismatch: safety_tick expects {expected} arguments, Panda passes {actual}')
  return expected

def self_test():
  assert check('void safety_tick(const safety_config *cfg) {}',
               'safety_tick(&current_safety_config);') == 1
  assert check('void safety_tick(void) {}', 'safety_tick();') == 0
  try:
    check('void safety_tick(const safety_config *cfg) {}', 'safety_tick();')
  except ValueError:
    pass
  else:
    raise AssertionError('The reported mismatched pair must fail')
  print('PASS: safety_tick interface regression cases')

def check_health(header, source):
  """Compile the real state converter against pinned health_t, with a message stub.

  This checks the firmware-facing fields and flags, not Cap'n Proto or a full
  pandad build. Exercise both reset bits independently and the legacy ABI.
  """
  start = source.index('void fill_panda_state(')
  end = source.index('\n}\n', start) + 3
  converter = source[start:end]
  setters = sorted(set(re.findall(r'ps\.(set\w+)\(', converter)))
  methods = '\n'.join(
    f'    template <typename T> void {name}(T) {{}}'
    for name in setters if name not in ('setNmiReset', 'setHardfaultReset'))
  stub = """
namespace cereal {
struct CarParams { enum class SafetyModel {}; };
struct PandaState {
  enum class PandaType {};
  enum class FaultStatus {};
  enum class HarnessStatus {};
  struct Builder {
    bool nmi = false, hardfault = false;
    void setNmiReset(bool value) { nmi = value; }
    void setHardfaultReset(bool value) { hardfault = value; }
METHODS
  };
};
}
""".replace('METHODS', methods)
  # Test the actual pin, then a legacy header and one exposing the newer bits.
  legacy = re.sub(r'^#define HEALTH_FLAG_(?:NMI_RESET|HARDFAULT_RESET).*\n',
                  '', header, flags=re.MULTILINE)
  modern = legacy + '\n#define HEALTH_FLAG_NMI_RESET (1U << 7)\n#define HEALTH_FLAG_HARDFAULT_RESET (1U << 8)\n'
  with tempfile.TemporaryDirectory(prefix='panda-health-') as directory:
    root = Path(directory)
    for name, health in [('pinned', header), ('legacy', legacy), ('modern', modern)]:
      (root / 'health.h').write_text(health)
      expected_nmi = 'HEALTH_FLAG_NMI_RESET' in health
      expected_hardfault = 'HEALTH_FLAG_HARDFAULT_RESET' in health
      assertions = f"""
int main() {{
  for (unsigned flags = 0; flags <= 65535; ++flags) {{
    health_t health{{}};
    health.flags_pkt = flags;
    cereal::PandaState::Builder ps;
    fill_panda_state(ps, cereal::PandaState::PandaType{{}}, health);
    assert(ps.nmi == ({str(expected_nmi).lower()} && ((flags & (1U << 7)) != 0)));
    assert(ps.hardfault == ({str(expected_hardfault).lower()} && ((flags & (1U << 8)) != 0)));
  }}
}}
"""
      code = '#include <cstdint>\n#include <cassert>\n#include "health.h"\n' + stub + converter + assertions
      (root / 'check.cc').write_text(code)
      subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                      str(root / 'check.cc'), '-o', str(root / 'check')], check=True)
      subprocess.run([str(root / 'check')], check=True)
      print(f'PASS: compiled Panda health converter ({name}; all 65536 flag values)')

def main():
  if sys.argv[1:] == ['--self-test']:
    self_test()
    return
  if sys.argv[1:]:
    raise ValueError('Usage: check_panda_compat.py [--self-test]')
  count = check((ROOT / 'opendbc_repo/opendbc/safety/safety.h').read_text(),
                (ROOT / 'panda/board/main.c').read_text())
  print(f'PASS: Panda/opendbc safety_tick interface ({count} arguments)')
  check_health((ROOT / 'panda/board/health.h').read_text(),
               (ROOT / 'openpilot/selfdrive/pandad/pandad.cc').read_text())
  print('This does not replace a full pandad build, firmware compilation or vehicle safety tests.')

if __name__ == '__main__':
  try:
    main()
  except (OSError, ValueError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
