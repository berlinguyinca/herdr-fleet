"""Shared helpers: real example files, mutated copies written to real temp files."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
LOCK_EXAMPLE = EXAMPLES / "fleet.lock.example.yaml"
CONFIG_EXAMPLE = EXAMPLES / "fleet-config.example.yaml"

Doc = dict[str, object]


def read_yaml(path: Path) -> Doc:
    loaded: Doc = yaml.safe_load(path.read_text())
    return loaded


def example_lock() -> Doc:
    return copy.deepcopy(read_yaml(LOCK_EXAMPLE))


def example_config() -> Doc:
    return copy.deepcopy(read_yaml(CONFIG_EXAMPLE))


def write_yaml(path: Path, doc: Doc) -> Path:
    path.write_text(yaml.safe_dump(doc))
    return path
