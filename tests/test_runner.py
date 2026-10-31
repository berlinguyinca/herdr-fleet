"""run_playbook turns an unrunnable ansible-playbook into a result instead of a traceback."""

from __future__ import annotations

from pathlib import Path

from herdr_fleet.runner import EXIT_COMMAND_NOT_RUNNABLE, Mode, run_playbook


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
