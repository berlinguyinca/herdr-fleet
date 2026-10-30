"""Run the real CLI as a subprocess against the real example files."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE, example_config, example_lock, write_yaml


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "herdr_fleet.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def inputs() -> list[str]:
    return ["--lock", str(LOCK_EXAMPLE), "--config", str(CONFIG_EXAMPLE)]


def test_validate_examples_ok() -> None:
    result = run("validate", *inputs())
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_validate_json_ok() -> None:
    result = run("validate", *inputs(), "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"valid": True, "errors": []}


def test_validate_failure_exit_1_and_message(tmp_path: Path) -> None:
    doc = example_lock()
    doc["repos"]["autospec"] = "main"  # type: ignore[index]
    lock = write_yaml(tmp_path / "fleet.lock", doc)
    result = run("validate", "--lock", str(lock), "--config", str(CONFIG_EXAMPLE))
    assert result.returncode == 1
    assert "repos.autospec" in result.stdout + result.stderr


def test_validate_failure_json(tmp_path: Path) -> None:
    doc = example_config()
    doc["bogus"] = 1
    config = write_yaml(tmp_path / "c.yaml", doc)
    result = run("validate", "--lock", str(LOCK_EXAMPLE), "--config", str(config), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert any("bogus" in e for e in payload["errors"])


def test_validate_reports_errors_from_both_files(tmp_path: Path) -> None:
    lock_doc = example_lock()
    lock_doc["clis"]["codex"] = "latest"  # type: ignore[index]
    config_doc = example_config()
    config_doc["bogus"] = 1
    result = run(
        "validate",
        "--lock",
        str(write_yaml(tmp_path / "l", lock_doc)),
        "--config",
        str(write_yaml(tmp_path / "c", config_doc)),
        "--json",
    )
    assert len(json.loads(result.stdout)["errors"]) == 2


def test_missing_file_exit_1(tmp_path: Path) -> None:
    result = run("validate", "--lock", str(tmp_path / "x"), "--config", str(CONFIG_EXAMPLE))
    assert result.returncode == 1
    assert "not found" in result.stdout


def test_plan_text_lists_ordered_roles_and_notice() -> None:
    result = run("plan", *inputs())
    assert result.returncode == 0
    out = result.stdout
    assert "no changes made, check-mode execution not implemented yet (slice 2)" in out
    assert out.index("baseline") < out.index("toolchain") < out.index("ci-server")
    assert "host-a" in out and "host-b" in out


def test_plan_json() -> None:
    result = run("plan", *inputs(), "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["changes_made"] is False
    assert "slice 2" in payload["notice"]
    steps = payload["applications"]
    assert steps[0] == {"step": 1, "host": "host-a", "role": "baseline"}
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))


def test_plan_invalid_inputs_exit_1_without_plan(tmp_path: Path) -> None:
    doc = example_config()
    doc["services"]["ci_server"].pop("ingress")  # type: ignore[index,union-attr]
    config = write_yaml(tmp_path / "c.yaml", doc)
    result = run("plan", "--lock", str(LOCK_EXAMPLE), "--config", str(config), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert "applications" not in payload


def test_no_command_is_usage_error() -> None:
    assert run().returncode == 2
