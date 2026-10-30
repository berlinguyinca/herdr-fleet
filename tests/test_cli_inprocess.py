"""Same real CLI and real files, called in-process so coverage sees it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from herdr_fleet.cli import main
from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE, example_config, write_yaml

BASE = ["--lock", str(LOCK_EXAMPLE), "--config", str(CONFIG_EXAMPLE)]


def test_validate_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", *BASE]) == 0
    assert "OK" in capsys.readouterr().out


def test_plan_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", *BASE]) == 0
    out = capsys.readouterr().out
    assert "host-a: baseline" in out
    assert "slice 2" in out


def test_plan_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", *BASE, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changes_made"] is False


def test_invalid_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = example_config()
    doc["bogus"] = 1
    config = write_yaml(tmp_path / "c.yaml", doc)
    args = ["--lock", str(LOCK_EXAMPLE), "--config", str(config)]
    assert main(["validate", *args]) == 1
    assert "INVALID" in capsys.readouterr().out
    assert main(["plan", *args, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False
