#!/usr/bin/env python3
"""Install a pinned official CommaView release with an explicit HKG fork adapter.

Only two downloaded shell helpers gain exact fork URL entries. No origin URL,
binary, account, pairing, bundle verification or parked-install gate is changed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = HERE / 'release.json'
ADAPTER_MARKER = '# HKG Jetlink compatibility: explicit sunnypilot-derived repositories'
ADAPTER_CASE = '''    *github.com:fanhaohsiang/sunnypilot|*github.com/fanhaohsiang/sunnypilot|*github.com:tonybinhewu/sunnypilot|*github.com/tonybinhewu/sunnypilot) printf '%s\\n' sunnypilot ;;
'''
TARGETS = ('openpilot/selfdrive/ui/ui_state.py',
           'openpilot/selfdrive/ui/mici/onroad/augmented_road_view.py',
           'openpilot/selfdrive/ui/onroad/augmented_road_view.py')


def run(args, **kwargs):
  return subprocess.run(args, check=True, **kwargs)


def download_kit(stage, manifest, cache=None):
  def fetch(item):
    name, expected = item
    target = stage / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if cache is not None:
      shutil.copyfile(cache / name, target)
    else:
      run(['curl', '-fsSL', '--proto', '=https', '--proto-redir', '=https', '--retry', '2', '--max-time', '40',
           manifest['source'] + '/' + name, '--output', str(target)])
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
      raise ValueError('Official installer file changed; refusing to run: ' + name)
  with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(fetch, manifest['files'].items()))


def adapt_helpers(stage):
  for name in ('apply_onroad_ui_export_patch.sh', 'verify_onroad_ui_export_patch.sh'):
    path = stage / 'scripts' / name
    source = path.read_text()
    start = source.index('remote_flavor() {')
    end = source.index('\n}\n', start)
    function = source[start:end]
    anchor = '  case "$remote" in\n'
    if function.count(anchor) != 1:
      raise ValueError('Unexpected fork-detection layout in ' + name)
    function = function.replace(anchor, anchor + '    ' + ADAPTER_MARKER + '\n' + ADAPTER_CASE, 1)
    path.write_text(source[:start] + function + source[end:])
    run(['bash', '-n', str(path)])
    # Exercise the real downloaded fork classifier, including a negative case.
    function = path.read_text()[start:path.read_text().index('\n}\n', start) + 3]
    for remote in ('https://github.com/FANHAOHSIANG/sunnypilot.git', 'git@github.com:TonyBinheWu/sunnypilot.git',
                   'https://github.com/sunnypilot/sunnypilot.git', 'https://github.com/unknown/other.git'):
      result = subprocess.run(['bash', '-c', function + '\nremote_flavor "$1"', '--', remote], capture_output=True, text=True)
      expected = remote != 'https://github.com/unknown/other.git'
      if (result.returncode == 0 and result.stdout.strip() == 'sunnypilot') != expected:
        raise ValueError('Fork adapter check failed: ' + remote)
  print('PASS: explicit HKG fork adapter; unrelated repositories stay unsupported', flush=True)


def check_hooks(stage, root, platform):
  # Test the official transformer on disposable UI copies, never on the running UI.
  with tempfile.TemporaryDirectory(prefix='commaview-hkg-check-') as directory:
    test_root = Path(directory)
    for relative in TARGETS:
      source = root / relative
      destination = test_root / relative
      destination.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(source, destination)
    transformer = stage / 'scripts/transform_onroad_ui_export.py'
    run([sys.executable, str(transformer), '--op-root', str(test_root), '--flavor', 'sunnypilot', '--platform', platform])
    for path in test_root.rglob('*.py'):
      compile(path.read_text(), str(path), 'exec')
    helper = test_root / 'openpilot/selfdrive/ui/commaview_export.py'
    run([sys.executable, str(stage / 'scripts/smoke_onroad_ui_export_helper.py'), str(helper), 'SUNNYPILOT'])
  print('PASS: official CommaView UI hook transforms and smoke check', flush=True)


def install(stage, manifest, root):
  # The existing official installer enforces parked/disengaged twice, verifies
  # the ARM64 bundle and maintains backups/rollback. Do not weaken those gates.
  remote = subprocess.check_output(['git', '-C', str(root), 'remote', 'get-url', 'origin'], text=True).strip().lower()
  normalized = remote.removesuffix('/').removesuffix('.git').replace('git@github.com:', 'https://github.com/')
  if normalized not in ('https://github.com/fanhaohsiang/sunnypilot', 'https://github.com/tonybinhewu/sunnypilot'):
    raise ValueError('This adapter only supports the reviewed HKG sunnypilot repositories')
  model = Path('/sys/firmware/devicetree/base/model')
  if not model.exists():
    raise ValueError('--install must run on your comma; --check works on a computer')
  # Refuse unrelated local UI changes before the official installer's force-repair.
  classified = subprocess.check_output([sys.executable, str(stage / 'scripts/transform_onroad_ui_export.py'),
    '--op-root', str(root), '--flavor', 'sunnypilot', '--classify', *TARGETS], text=True)
  if any(line.startswith(('foreign ', 'damaged ')) for line in classified.splitlines()):
    raise ValueError('UI files have unrelated local changes or damage; resolve them before installation')
  env = dict(os.environ, COMMAVIEWD_INSTALLER_RAW_BASE=stage.as_uri(), COMMAVIEWD_OP_ROOT=str(root))
  # Companions are already SHA256-verified and staged; the actual runtime bundle
  # still comes directly from the official release's HTTPS origin.
  run(['bash', str(stage / 'install.sh'), '--tag', manifest['runtime_tag']], env=env)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  mode = parser.add_mutually_exclusive_group()
  mode.add_argument('--check', action='store_true', help='Check compatibility without installing (default)')
  mode.add_argument('--install', action='store_true', help='Install on comma while parked and disengaged')
  parser.add_argument('--platform', choices=('mici', 'tizi'), default='mici')
  parser.add_argument('--cache', type=Path, help='Use a previously downloaded kit; hashes are still verified')
  args = parser.parse_args()
  manifest = json.loads(MANIFEST.read_text())
  print('CommaView', manifest['runtime_tag'], 'for app', manifest['app_tag'], flush=True)
  with tempfile.TemporaryDirectory(prefix='commaview-hkg-install-') as directory:
    stage = Path(directory)
    download_kit(stage, manifest, args.cache)
    adapt_helpers(stage)
    check_hooks(stage, ROOT, args.platform)
    if args.install:
      install(stage, manifest, ROOT)
    else:
      print('Compatibility check passed. Runtime/Android live streaming still requires device validation.')


if __name__ == '__main__':
  try:
    main()
  except (OSError, ValueError, subprocess.CalledProcessError) as exc:
    print('CommaView setup stopped:', exc, file=sys.stderr)
    raise SystemExit(1)
