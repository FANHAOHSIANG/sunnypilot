#!/usr/bin/env bash
# Runs actual integration in an isolated exported source tree. No car access.
set -euo pipefail
PKG=$(cd "$(dirname "$0")" && pwd)
SOURCE=${1:?pass pinned opendbc source checkout}
RESULTS=${2:?pass results parent directory}
mkdir -p "$RESULTS"
RESULTS=$(cd "$RESULTS" && pwd)
python "$PKG/tests/test_validation_reporting.py" 2>&1 | tee "$RESULTS/validation-reporting.log"
python "$PKG/validate_all.py" --source "$SOURCE" --results "$RESULTS/full-integration"
