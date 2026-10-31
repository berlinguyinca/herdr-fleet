"""``plan --check`` / ``apply`` in-process (so coverage sees them) against the real ansible.

Read-only or failing-fast only: HOME is an empty temporary directory, or an unwritable one for
the failure path, so nothing is installed and the real HOME is never touched.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from herdr_fleet.cli import main
from herdr_fleet.runner import ansible_dir, ansible_playbook_executable
from tests.helpers import example_config, example_lock, write_yaml


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
    args = [*inputs(tmp_path), "--connection", "local"]
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
