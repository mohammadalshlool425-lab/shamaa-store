#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then PYTHON=python; fi
"$PYTHON" -m compileall -q server tests scripts
for file in static/*.js; do node --check "$file"; done
"$PYTHON" -m pytest --cov=server --cov-report=term-missing --cov-report=xml:data/coverage.xml --cov-fail-under=90
