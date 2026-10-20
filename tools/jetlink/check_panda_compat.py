#!/usr/bin/env python3
"""Check the Panda/opendbc safety_tick calling interface, not vehicle safety behavior."""
from pathlib import Path
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

def main():
  if sys.argv[1:] == ['--self-test']:
    self_test()
    return
  if sys.argv[1:]:
    raise ValueError('Usage: check_panda_compat.py [--self-test]')
  count = check((ROOT / 'opendbc_repo/opendbc/safety/safety.h').read_text(),
                (ROOT / 'panda/board/main.c').read_text())
  print(f'PASS: Panda/opendbc safety_tick interface ({count} arguments)')
  print('This does not replace firmware compilation or vehicle safety tests.')

if __name__ == '__main__':
  try:
    main()
  except (OSError, ValueError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
