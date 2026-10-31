"""Stdout callback: one JSON object per line, consumed by ``herdr_fleet.report``.

Events: ``{"event": "task", ...}`` for every task result that matters (anything but a plain
conditional skip) and ``{"event": "fingerprint", ...}`` when a task sets ``fleet_fingerprint``.
"""

from __future__ import annotations

import json
from typing import Any

from ansible.executor.task_result import TaskResult
from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    name: fleet_json
    type: stdout
    short_description: JSON-lines events for herdr-fleet
    description: Emits one JSON object per task result.
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "fleet_json"

    def _emit(self, payload: dict[str, Any]) -> None:
        self._display.display(json.dumps(payload, default=str, sort_keys=True))

    def _task_event(self, result: TaskResult, status: str) -> None:
        body = result._result
        role = result._task._role
        role_name = role.get_name() if role else None
        task_name = result._task.get_name()
        if role_name and task_name.startswith(f"{role_name} : "):
            task_name = task_name[len(role_name) + 3 :]
        self._emit(
            {
                "event": "task",
                "host": result._host.get_name(),
                "role": role_name,
                "task": task_name,
                "status": status,
                "msg": str(body.get("msg", "")),
                "diff": _diffs(body),
            }
        )
        facts = body.get("ansible_facts", {})
        if isinstance(facts, dict) and "fleet_fingerprint" in facts:
            self._emit(
                {
                    "event": "fingerprint",
                    "host": result._host.get_name(),
                    "fingerprint": facts["fleet_fingerprint"],
                }
            )

    def v2_runner_on_ok(self, result: TaskResult, **kwargs: Any) -> None:
        self._task_event(result, "changed" if result._result.get("changed") else "ok")

    def v2_runner_on_failed(self, result: TaskResult, ignore_errors: bool = False) -> None:
        if not ignore_errors:
            self._task_event(result, "failed")

    def v2_runner_on_unreachable(self, result: TaskResult) -> None:
        self._task_event(result, "unreachable")

    def v2_runner_on_skipped(self, result: TaskResult) -> None:
        # In check mode a module that cannot predict its effect is skipped: it would have run.
        if "check mode" in str(result._result.get("msg", "")).lower():
            self._task_event(result, "would_change")


def _diffs(body: dict[str, Any]) -> list[Any]:
    found: list[Any] = []
    if body.get("diff"):
        found.append(body["diff"])
    for item in body.get("results", []):
        if isinstance(item, dict) and item.get("diff"):
            found.append(item["diff"])
    return found
