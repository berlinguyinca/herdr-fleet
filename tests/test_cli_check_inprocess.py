"""``plan --check`` / ``apply`` in-process (so coverage sees them) against the real ansible.

Read-only or failing-fast only: HOME is an empty temporary directory, or an unwritable one for
the failure path, so nothing is installed and the real HOME is never touched.
"""

from __future__ import annotations

import json
import os
import pty
import stat
import sys
from pathlib import Path

import pytest

from herdr_fleet.cli import main
from herdr_fleet.runner import ansible_dir, ansible_playbook_executable
from tests.helpers import LOCK_EXAMPLE, example_config, example_lock, write_yaml


def inputs(tmp_path: Path, *, jump_host: str | None = None) -> list[str]:
    config = example_config()
    host_b = config["hosts"]["host-b"]  # type: ignore[index]
    host_b["roles"] = ["toolchain"]
    host_b["connection"]["jump_host"] = jump_host  # type: ignore[index]
    if jump_host is None:
        del host_b["connection"]["jump_host"]
    config["hosts"] = {"host-a": config["hosts"]["host-a"], "host-b": host_b}  # type: ignore[index]
    config["hosts"]["host-a"]["roles"] = ["baseline", "toolchain"]  # type: ignore[index]
    config["services"] = {}
    lock = example_lock()
    return [
        "--lock",
        str(write_yaml(tmp_path / "fleet.lock", lock)),
        "--config",
        str(write_yaml(tmp_path / "fleet-config.yaml", config)),
    ]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def test_packaged_ansible_content_and_executable_exist() -> None:
    assert (ansible_dir() / "playbooks" / "converge.yml").is_file()
    assert (ansible_dir() / "roles" / "toolchain" / "tasks" / "main.yml").is_file()
    assert ansible_playbook_executable().is_file()


def test_check_json_and_text_in_process(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [*inputs(tmp_path), "--connection", "local", "--limit", "host-a"]
    assert main(["plan", "--check", *args, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 2
    assert [h["host"] for h in payload["hosts"]] == ["host-a"]
    assert not (home / ".local").exists()

    assert main(["plan", "--check", *args]) == 2
    out = capsys.readouterr().out
    assert "host-a: DRIFT" in out and "plan --check: drift found (exit 2)" in out
    assert "baseline: not implemented" in out


def test_unknown_jump_host_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", "--check", *inputs(tmp_path, jump_host="ghost")]) == 1
    assert "jump_host 'ghost'" in capsys.readouterr().out


def test_apply_requires_yes(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    args = [*inputs(tmp_path), "--connection", "local", "--limit", "host-a"]
    assert main(["apply", *args]) == 1
    assert "--yes" in capsys.readouterr().out
    assert not (home / ".local").exists()


def test_apply_failure_stops_and_is_not_verified(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home.chmod(stat.S_IRUSR | stat.S_IXUSR)  # cannot create ~/.local: the first task fails
    try:
        args = [*inputs(tmp_path), "--connection", "local", "--limit", "host-a", "--yes", "--json"]
        assert main(["apply", *args]) == 1
        payload = json.loads(capsys.readouterr().out)
    finally:
        home.chmod(stat.S_IRWXU)
    assert payload["mode"] == "apply" and payload["exit_code"] == 1
    assert payload["errors"] or any(h["status"] == "failed" for h in payload["hosts"])


def test_apply_with_local_connection_requires_limit(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["apply", *inputs(tmp_path), "--connection", "local", "--yes"]) == 1
    assert "--limit" in capsys.readouterr().out
    assert not (home / ".local").exists()


def test_confirmation_prompt_goes_to_stderr_and_lists_hosts(
    tmp_path: Path,
    home: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = pty.openpty()
    os.write(master, b"no\n")
    try:
        with os.fdopen(slave) as terminal:
            monkeypatch.setattr(sys, "stdin", terminal)
            args = [*inputs(tmp_path), "--connection", "local", "--limit", "host-a", "--json"]
            assert main(["apply", *args]) == 1
    finally:
        os.close(master)
    captured = capsys.readouterr()
    assert "host-a: baseline, toolchain" in captured.err and "Type 'yes'" in captured.err
    assert "host-b" not in captured.err
    assert json.loads(captured.out)["valid"] is False  # stdout stays pure JSON
    assert not (home / ".local").exists()


def test_apply_stops_at_the_first_failed_host_and_reports_the_rest_not_attempted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = example_config()
    for name in ("host-a", "host-b"):
        config["hosts"][name]["roles"] = ["toolchain"]  # type: ignore[index]
    config["hosts"]["host-a"]["connection"]["address"] = "127.0.0.1"  # type: ignore[index]
    config["hosts"]["host-b"]["connection"]["address"] = "127.0.0.2"  # type: ignore[index]
    del config["hosts"]["host-b"]["connection"]["jump_host"]  # type: ignore[index]
    config["services"] = {}
    monkeypatch.setenv("ANSIBLE_REMOTE_PORT", "1")  # ssh to a closed local port: unreachable
    args = [
        "--lock",
        str(LOCK_EXAMPLE),
        "--config",
        str(write_yaml(tmp_path / "c.yaml", config)),
        "--yes",
        "--json",
    ]
    assert main(["apply", *args]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert [(h["host"], h["status"]) for h in payload["hosts"]] == [
        ("host-a", "failed"),
        ("host-b", "not-attempted"),
    ]
