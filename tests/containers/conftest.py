"""Session fixtures: one running container per runtime with herdr-fleet installed in it."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.containers.runtime import RUNTIMES, Completed, ContainerRuntime

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOME = "/home/fleet"
LOCK_PATH = f"{HOME}/inputs/fleet.lock"
CONFIG_PATH = f"{HOME}/inputs/fleet-config.yaml"
PREFIX = f"{HOME}/.local/share/herdr-fleet/toolchain"
CLEAN_STATE = f"""
set -e
find {PREFIX} -mindepth 1 -maxdepth 1 ! -name cache -exec rm -rf {{}} + 2>/dev/null || true
rm -f {HOME}/.local/bin/pi {HOME}/.local/bin/codex
rm -rf {HOME}/.nvm {HOME}/decoy
cp -f /etc/skel/.profile {HOME}/.profile
"""

BOOTSTRAP = f"""
set -e
mkdir -p {HOME}/tmp {HOME}/src {HOME}/inputs
cp -rn /etc/skel/. {HOME}/
tar -C /repo --exclude=.venv --exclude=.git --exclude=.coverage --exclude=.omc \
    --exclude=.pytest_cache --exclude=.ruff_cache --exclude='*.egg-info' -cf - . \
  | tar -C {HOME}/src -xf -
uv venv --quiet --python 3.12 {HOME}/venv
uv pip install --quiet --python {HOME}/venv/bin/python {HOME}/src
herdr-fleet validate --lock /repo/tests/containers/fixtures/fleet.lock.yaml \
  --config /repo/tests/containers/fixtures/fleet-config.yaml
"""


class Fleet:
    """The herdr-fleet CLI as run inside one container."""

    def __init__(self, runtime: ContainerRuntime) -> None:
        self.runtime = runtime
        self.lock_text = (FIXTURES / "fleet.lock.yaml").read_text()

    def reset(self) -> None:
        done = self.runtime.sh(CLEAN_STATE)
        assert done.returncode == 0, done.stderr
        self.use_lock(self.lock_text)
        self.runtime.write_file(CONFIG_PATH, (FIXTURES / "fleet-config.yaml").read_text())

    def use_lock(self, text: str, name: str = "fleet.lock") -> str:
        path = f"{HOME}/inputs/{name}"
        self.runtime.write_file(path, text)
        return path

    def run(
        self, command: str, *extra: str, lock: str = LOCK_PATH, json_out: bool = True
    ) -> Completed:
        args = [
            "herdr-fleet", command, "--lock", lock, "--config", CONFIG_PATH,
            "--connection", "local", *extra,
        ]  # fmt: skip
        if json_out:
            args.append("--json")
        return self.runtime.exec(args)

    def check(self, *, lock: str = LOCK_PATH) -> Completed:
        return self.run("plan", "--check", lock=lock)

    def apply(self, *, lock: str = LOCK_PATH) -> Completed:
        return self.run("apply", "--yes", lock=lock)

    def converge(self) -> None:
        """Ensure the pinned fixture lock is applied; cheap when it already is."""
        if self.check().returncode != 0:
            done = self.apply()
            assert done.returncode == 0, done.stdout + done.stderr


def toolchain_role(result: Completed) -> dict[str, object]:
    payload = json.loads(result.stdout)
    (host,) = payload["hosts"]
    return next(r for r in host["roles"] if r["role"] == "toolchain")


def difference_fields(result: Completed) -> list[str]:
    differences = toolchain_role(result)["differences"]
    assert isinstance(differences, list)
    return [d["field"] for d in differences]


@pytest.fixture(scope="session", params=[cls.name for cls in RUNTIMES])
def runtime(request: pytest.FixtureRequest) -> Iterator[ContainerRuntime]:
    cls = next(c for c in RUNTIMES if c.name == request.param)
    reason = cls.unavailable_reason()
    if reason is not None:
        pytest.skip(f"{cls.name} unavailable: {reason}")
    instance = cls()
    try:
        instance.start()
        boot = instance.sh(BOOTSTRAP, timeout=1200)
        assert boot.returncode == 0, f"bootstrap failed:\n{boot.stdout}\n{boot.stderr}"
        yield instance
    finally:
        removed = instance.stop()
        print(f"[container-harness] removed: {', '.join(removed) or 'nothing'}")


@pytest.fixture
def fleet(runtime: ContainerRuntime) -> Fleet:
    return Fleet(runtime)
