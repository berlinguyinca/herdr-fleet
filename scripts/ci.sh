#!/usr/bin/env bash
# CI-agnostic gate. Assumes the dev extras are installed: pip install -e '.[dev]'
set -euo pipefail
cd "$(dirname "$0")/.."

ruff check .
ruff format --check .
pytest
herdr-fleet validate --lock examples/fleet.lock.example.yaml --config examples/fleet-config.example.yaml
herdr-fleet plan --lock examples/fleet.lock.example.yaml --config examples/fleet-config.example.yaml > /dev/null

# Container tests build an image and download Node/npm packages, so they need docker and network.
# Apptainer cases skip with a reason when apptainer is absent (see docs/testing.md).
if command -v docker > /dev/null 2>&1 && docker info > /dev/null 2>&1; then
  echo "ci.sh: docker available, running container tests (pytest -m container)"
  pytest -m container --no-cov
else
  echo "ci.sh: docker not available, SKIPPING container tests (pytest -m container)"
fi
