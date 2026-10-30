#!/usr/bin/env bash
# CI-agnostic gate. Assumes the dev extras are installed: pip install -e '.[dev]'
set -euo pipefail
cd "$(dirname "$0")/.."

ruff check .
ruff format --check .
pytest
herdr-fleet validate --lock examples/fleet.lock.example.yaml --config examples/fleet-config.example.yaml
herdr-fleet plan --lock examples/fleet.lock.example.yaml --config examples/fleet-config.example.yaml > /dev/null
