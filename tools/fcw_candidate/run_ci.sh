#!/usr/bin/env bash
# Isolated compile/regression only. No root, device, ECU or firmware access.
set -euo pipefail
PKG=$(cd "$(dirname "$0")" && pwd)
SOURCE=${1:?pass pinned opendbc source checkout}
RESULTS=${2:?pass results directory}
mkdir -p "$RESULTS"
RESULTS=$(cd "$RESULTS" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python -m unittest discover -s "$PKG/tests" -p 'test_controller_proxy.py' -v 2>&1 | tee "$RESULTS/controller.log"
python -m unittest discover -s "$PKG/tests" -p 'test_native_policy.py' -v 2>&1 | tee "$RESULTS/native-policy.log"
python "$PKG/prepare_candidate.py" --source "$SOURCE" --output "$TMP/opendbc"
cp "$TMP/opendbc/FCW_CANDIDATE.patch" "$RESULTS/candidate.patch"
cp "$TMP/opendbc/FCW_CANDIDATE.json" "$RESULTS/candidate-manifest.json"
cd "$TMP/opendbc"
export PYTHONPATH="$PWD"
python -m compileall -q opendbc
# Full existing safety suite, not only Hyundai: preserve tests and all results.
python -m unittest discover -s opendbc/safety/tests 2>&1 | tee "$RESULTS/full-safety-production.log"
FCW_EXPECT_BENCH=0 python "$PKG/tests/test_full_libsafety_candidate.py" 2>&1 | tee "$RESULTS/production-lock.log"
# This one definition is ONLY in a temporary host-side test translation unit.
# It is NOT added to the firmware build system or device configuration.
python - <<'PY'
from pathlib import Path
p=Path('opendbc/safety/tests/libsafety/safety.c')
p.write_text('#define HYUNDAI_FCW_BENCH 1\n'+p.read_text())
PY
python -m unittest opendbc.safety.tests.test_hyundai_canfd 2>&1 | tee "$RESULTS/hyundai-bench-baseline.log"
FCW_EXPECT_BENCH=1 python "$PKG/tests/test_full_libsafety_candidate.py" 2>&1 | tee "$RESULTS/paired-integration.log"
printf '%s\n' 'SOFTWARE TESTS FINISHED; NO HARDWARE/ROAD AUTHORIZATION' | tee "$RESULTS/conclusion.txt"
