"""``plan --check`` and ``apply`` through the real CLI and the real ansible-playbook.

Everything here is read-only: check mode against this machine with HOME pointed at an empty
temporary directory, so nothing is installed and the real HOME is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE, example_config, example_lock, write_yaml


def run(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, "-m", "herdr_fleet.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def single_host_inputs(tmp_path: Path) -> list[str]:
    config = example_config()
    config["hosts"] = {"host-a": config["hosts"]["host-a"]}  # type: ignore[index]
    config["services"]["ci_server"]["ingress"] = "tunnel"  # type: ignore[index]
    lock = example_lock()
    lock["node"]["sha256"] = {  # type: ignore[index]
        "linux-x64": "0" * 63 + "1",
        "linux-arm64": "0" * 63 + "2",
        "darwin-arm64": "0" * 63 + "3",
        "darwin-x64": "0" * 63 + "4",
    }
    return [
        "--lock",
        str(write_yaml(tmp_path / "fleet.lock", lock)),
        "--config",
        str(write_yaml(tmp_path / "fleet-config.yaml", config)),
    ]


def test_check_on_a_bare_home_reports_drift_exit_2(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = run(
        "plan", "--check", "--connection", "local", "--json", *single_host_inputs(tmp_path),
        home=home,
    )  # fmt: skip
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 2 and payload["mode"] == "check"
    (host,) = payload["hosts"]
    toolchain = next(r for r in host["roles"] if r["role"] == "toolchain")
    assert toolchain["status"] == "drift"
    fields = {d["field"] for d in toolchain["differences"]}
    assert "node" in fields and "cli pi-cli" in fields and "cli codex" in fields
    assert not (home / ".local").exists(), "check mode must not create anything"


def test_check_text_output_names_the_drift(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = run(
        "plan", "--check", "--connection", "local", *single_host_inputs(tmp_path), home=home
    )
    assert result.returncode == 2
    assert "host-a: DRIFT" in result.stdout
    assert "node: expected 22.11.0, found missing" in result.stdout
    assert "baseline: not implemented" in result.stdout


def test_check_unknown_limit_is_an_error_exit_1(tmp_path: Path) -> None:
    result = run(
        "plan", "--check", "--limit", "host-z", "--json", *single_host_inputs(tmp_path),
        home=tmp_path,
    )  # fmt: skip
    assert result.returncode == 1
    assert "host-z" in json.loads(result.stdout)["errors"][0]


def test_check_with_invalid_inputs_exits_1(tmp_path: Path) -> None:
    result = run(
        "plan", "--check", "--lock", str(tmp_path / "nope"), "--config", str(CONFIG_EXAMPLE),
        home=tmp_path,
    )  # fmt: skip
    assert result.returncode == 1
    assert "not found" in result.stdout


def test_unreachable_host_is_an_error_not_drift(tmp_path: Path) -> None:
    lock_path = single_host_inputs(tmp_path)[1]
    config = example_config()
    config["hosts"] = {
        "host-a": {
            "os": "debian",
            "roles": ["toolchain"],
            "connection": {"address": "127.0.0.1", "user": "no-such-user-herdr-fleet"},
        }
    }
    config["services"] = {}
    config_path = write_yaml(tmp_path / "ssh-config.yaml", config)
    result = run(
        "plan",
        "--check",
        "--json",
        "--lock",
        lock_path,
        "--config",
        str(config_path),
        home=tmp_path,
    )
    assert result.returncode == 1, result.stdout
    assert json.loads(result.stdout)["exit_code"] == 1


def test_plain_plan_rejects_check_only_options(tmp_path: Path) -> None:
    result = run(
        "plan",
        "--connection",
        "local",
        "--lock",
        str(LOCK_EXAMPLE),
        "--config",
        str(CONFIG_EXAMPLE),
        home=tmp_path,
    )
    assert result.returncode == 1
    assert "--check" in result.stdout


def test_apply_refuses_without_yes_when_not_a_tty(tmp_path: Path) -> None:
    result = run(
        "apply",
        "--connection",
        "local",
        "--lock",
        str(LOCK_EXAMPLE),
        "--config",
        str(CONFIG_EXAMPLE),
        home=tmp_path,
    )
    assert result.returncode == 1
    assert "--yes" in result.stdout
    assert not (tmp_path / ".local").exists()


def test_help_states_what_apply_does_not_do_yet(tmp_path: Path) -> None:
    result = run("apply", "--help", home=tmp_path)
    assert result.returncode == 0
    flat = " ".join(result.stdout.split())
    for missing in ("staggering", "session-awareness", "rollback", "notifications"):
        assert missing in flat
    assert "slice 4" in flat
