"""run_playbook turns an unrunnable ansible-playbook into a result instead of a traceback."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from herdr_fleet.config import load_lock
from herdr_fleet.inventory import build_extra_vars
from herdr_fleet.runner import EXIT_COMMAND_NOT_RUNNABLE, Mode, run_playbook
from tests.helpers import LOCK_EXAMPLE


def test_missing_executable_is_rc_127(tmp_path: Path) -> None:
    result = run_playbook({"all": {"hosts": {}}}, {}, Mode.CHECK, executable=tmp_path / "nope")
    assert result.returncode == EXIT_COMMAND_NOT_RUNNABLE
    assert "cannot run" in result.stderr and "nope" in result.stderr


def test_non_executable_file_is_rc_127(tmp_path: Path) -> None:
    plain = tmp_path / "ansible-playbook"
    plain.write_text("not executable")
    result = run_playbook({"all": {"hosts": {}}}, {}, Mode.CHECK, executable=plain)
    assert result.returncode == EXIT_COMMAND_NOT_RUNNABLE
    assert "Permission denied" in result.stderr


def _mixed_inventory(broken_home: Path) -> dict[str, object]:
    """a-bad fails in the role (its home is below a regular file); b-ok is a normal local host."""
    local = {
        "ansible_connection": "local",
        "ansible_python_interpreter": "auto_silent",
        "fleet_roles": ["toolchain"],
    }
    return {
        "all": {
            "hosts": {
                "a-bad": {**local, "toolchain_home": str(broken_home / "sub")},
                "b-ok": dict(local),
            }
        }
    }


def _hosts_seen(stdout: str) -> set[str]:
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    return {e["host"] for e in events}


def test_check_pass_keeps_going_after_a_failed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "file"
    broken.write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    extra = build_extra_vars(load_lock(LOCK_EXAMPLE))
    result = run_playbook(_mixed_inventory(broken), extra, Mode.CHECK)
    assert _hosts_seen(result.stdout) == {"a-bad", "b-ok"}


def test_apply_pass_stops_before_the_next_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "file"
    broken.write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    extra = build_extra_vars(load_lock(LOCK_EXAMPLE))
    result = run_playbook(_mixed_inventory(broken), extra, Mode.APPLY)
    assert _hosts_seen(result.stdout) == {"a-bad"}
    assert not (tmp_path / ".local").exists()


class _CountingHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.requests.append(self.path)
        self.send_response(404)
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def test_check_mode_does_not_download_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """plan --check is read-only: with Node absent it reports a pending change, no network."""
    _CountingHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HOME", str(tmp_path))
        extra = build_extra_vars(load_lock(LOCK_EXAMPLE))
        extra["toolchain_node_dist_url"] = f"http://127.0.0.1:{server.server_port}"
        # directories must already exist, or the download step is not reached in check mode
        prefix = tmp_path / ".local" / "share" / "herdr-fleet" / "toolchain"
        for sub in ("node", "bin", "cache"):
            (prefix / sub).mkdir(parents=True)
        (tmp_path / ".local" / "bin").mkdir(parents=True, exist_ok=True)
        result = run_playbook(_local_inventory(), extra, Mode.CHECK)
    finally:
        server.shutdown()
        server.server_close()
    assert _CountingHandler.requests == []
    lines = [json.loads(ln) for ln in result.stdout.splitlines() if ln.startswith("{")]
    events = [e for e in lines if e["event"] == "task"]
    assert not [e for e in events if e["status"] == "failed"], events
    pending = [e["task"] for e in events if e["status"] in ("changed", "would_change")]
    assert any("move node" in task for task in pending), pending
    assert list((prefix / "cache").iterdir()) == []


def _local_inventory() -> dict[str, object]:
    return {
        "all": {
            "hosts": {
                "h": {
                    "ansible_connection": "local",
                    "ansible_python_interpreter": "auto_silent",
                    "fleet_roles": ["toolchain"],
                }
            }
        }
    }
