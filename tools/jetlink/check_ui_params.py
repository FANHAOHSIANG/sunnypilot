#!/usr/bin/env python3
"""Check UI literal Params accesses and compile the real registry (schema enum stub)."""
import ast
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def missing_keys(source, registered):
  tree = ast.parse(source)
  missing = set()
  for call in ast.walk(tree):
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or not call.args:
      continue
    if call.func.attr not in {'get', 'get_bool', 'put', 'put_bool', 'remove', 'get_default_value'}:
      continue
    owner = call.func.value
    is_params = ((isinstance(owner, ast.Name) and owner.id == 'params') or
                 (isinstance(owner, ast.Attribute) and owner.attr == 'params') or
                 (isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name) and owner.func.id == 'Params'))
    key = call.args[0]
    if is_params and isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value not in registered:
      missing.add(key.value)
  return missing

def main():
  header = (ROOT / 'openpilot/common/params_keys.h').read_text()
  registered = set(re.findall(r'^\s*\{"([^"]+)",', header, re.MULTILINE))
  assert missing_keys('self.params.get_bool("ExperimentalMode")', set()) == {'ExperimentalMode'}
  assert not missing_keys('self.params.get_bool("ExperimentalMode")', {'ExperimentalMode'})
  failures = []
  for path in (ROOT / 'openpilot/selfdrive/ui').rglob('*.py'):
    for key in sorted(missing_keys(path.read_text(), registered)):
      failures.append(f'{path.relative_to(ROOT)}: unregistered Params key {key}')
  if failures:
    raise ValueError('\n'.join(failures))
  types = (ROOT / 'openpilot/common/params.h').read_text()
  types = types[types.index('enum ParamKeyFlag {'):types.index('class Params {')]
  with tempfile.TemporaryDirectory(prefix='ui-params-') as directory:
    root = Path(directory)
    schema = root / 'openpilot/cereal/gen/cpp'
    schema.mkdir(parents=True)
    (schema / 'log.capnp.h').write_text('namespace cereal { enum class LongitudinalPersonality { STANDARD = 1 }; }\n')
    code = '#include <cassert>\n#include <cstdint>\n#include <optional>\n#include <string>\n' + types + """
#include "openpilot/common/params_keys.h"
int main() {
  for (const auto *name : {"ExperimentalMode", "ExperimentalModeConfirmed", "DynamicExperimentalControl"}) {
    assert(keys.count(name) == 1);
    assert(keys.at(name).type == BOOL);
    assert(keys.at(name).flags == (PERSISTENT | BACKUP));
  }
  assert(keys.at("DynamicExperimentalControl").default_value == "0");
}
"""
    (root / 'check.cc').write_text(code)
    subprocess.run(['c++', '-std=c++17', '-I', str(root), '-I', str(ROOT),
                    str(root / 'check.cc'), '-o', str(root / 'check')], check=True)
    subprocess.run([str(root / 'check')], check=True)
  print('PASS: UI literal Params keys and compiled registry')
  print('Full UI imports, shared library rebuild and C4 boot remain unverified.')

if __name__ == '__main__':
  main()
