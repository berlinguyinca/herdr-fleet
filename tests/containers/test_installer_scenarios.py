"""Slice 3 installers (herdr, herdr-engineering, pi-runtime, baseline probes) in a real container.

Real downloads and real clones of public repositories, no mocks, stubs or fakes. Everything runs
as the unprivileged ``fleet`` user inside the container with its own HOME; the host's HOME,
sockets and processes are never involved. The real ``herdr`` binary is only ever asked for
``--version`` and ``status`` (which reports "no server" because none runs in the container), and
the scenarios assert that no herdr process was started.

Not coverable here, documented in docs/testing.md as the manual canary: systemd user units and
linger (the image has no user manager, so the fingerprint says "unmanaged"), and macOS.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest
import yaml

from tests.containers import pins
from tests.containers.conftest import FIXTURES, HOME
from tests.containers.runtime import Completed, ContainerRuntime

pytestmark = [pytest.mark.container, pytest.mark.network]

INPUTS = f"{HOME}/inputs"
CONFIG_PATH = f"{INPUTS}/installer-config.yaml"
BASE_LOCK_PATH = f"{INPUTS}/installer.lock"
CONFIG_TEXT = """\
version: 1
hosts:
  host-a:
    os: debian
    roles: [baseline, toolchain, herdr, herdr-engineering, pi-runtime]
    connection:
      address: localhost
      user: fleet
services:
  gateway:
    url: https://gateway.example.com
"""

HERDR = "0.9.1"
HERDR_SHA = "2a02fed16beb651ef006e1d43f048f652ca4dc58ad053cd2d44450563d5c54b7"
ENG = "637dd6837cb974c1c69aef2e5f72c63a440454d5"
PI = "60c6597391a6b60c11b8b95215e0ee040884c2aa"

HERDR_BIN = f"{HOME}/.local/bin/herdr"
ENG_ROOT = f"{HOME}/.local/share/herdr-engineering/herdr"
PI_ROOT = f"{HOME}/.local/share/herdr-engineering/pi"
SETTINGS = f"{HOME}/.pi/agent/settings.json"
GATE = f"{HOME}/.pi/agent/extensions/build-gate.ts"
FABRIC_ENV = f"{HOME}/.config/herdr-engineering/fabric.env"
FABRIC_LINE = "HERDR_ENGINEERING_FABRIC_URL=https://gateway.example.com"
RELEASE_GATE = f"{ENG_ROOT}/releases/{ENG}/deploy/pi-build-gate/build-gate.ts"
NEW_PI = f"{PI_ROOT}/releases/{pins.ALT_PI_ENGINEERING}"
NEW_ENG = f"{ENG_ROOT}/releases/{pins.ALT_HERDR_ENGINEERING}"
UNIT = f"{HOME}/.config/systemd/user/herdr-eng-fabric-agent.service"

# Everything the installers own, except the download caches (kept so scenarios stay fast).
CLEAN_INSTALLERS = f"""
set -e
rm -rf {HOME}/.local/share/herdr-engineering {HOME}/.venvs/herdr-engineering \
  {HOME}/.local/share/herdr-fleet/helpers {HOME}/.config/herdr-engineering \
  {HOME}/.config/systemd {HOME}/.pi
rm -f {HERDR_BIN} {HOME}/.local/bin/herdr-eng
"""

LockDoc = dict[str, dict[str, object]]


@dataclass
class Installers:
    """The herdr-fleet CLI as run inside one container against the installer fixture."""

    runtime: ContainerRuntime
    lock_text: str

    def reset(self) -> None:
        done = self.runtime.sh(CLEAN_INSTALLERS)
        assert done.returncode == 0, done.stderr
        self.runtime.write_file(BASE_LOCK_PATH, self.lock_text)
        self.runtime.write_file(CONFIG_PATH, CONFIG_TEXT)

    def use_lock(self, mutate: Callable[[LockDoc], object], name: str) -> str:
        doc: LockDoc = yaml.safe_load(self.lock_text)
        mutate(doc)
        path = f"{INPUTS}/{name}"
        self.runtime.write_file(path, yaml.safe_dump(doc))
        return path

    def run(self, command: str, *extra: str, lock: str = BASE_LOCK_PATH) -> Completed:
        return self.runtime.exec(
            [
                "herdr-fleet",
                command,
                "--lock",
                lock,
                "--config",
                CONFIG_PATH,
                "--connection",
                "local",
                *extra,
                "--json",
            ]  # fmt: skip
        )

    def check(self, *, lock: str = BASE_LOCK_PATH) -> Completed:
        return self.run("plan", "--check", lock=lock)

    def apply(self, *, lock: str = BASE_LOCK_PATH) -> Completed:
        return self.run("apply", "--yes", "--limit", "host-a", lock=lock)

    def converge(self) -> None:
        """Ensure the fixture lock is applied; cheap when it already is."""
        self.reset_inputs()
        if self.check().returncode != 0:
            done = self.apply()
            assert done.returncode == 0, done.stdout + done.stderr
            assert self.check().returncode == 0

    def reset_inputs(self) -> None:
        self.runtime.write_file(BASE_LOCK_PATH, self.lock_text)
        self.runtime.write_file(CONFIG_PATH, CONFIG_TEXT)


def role_messages(result: Completed, role: str) -> list[str]:
    (host,) = json.loads(result.stdout)["hosts"]
    found = next(r for r in host["roles"] if r["role"] == role)
    return [d["message"] for d in found["differences"]]


def sha_cmd(path: str) -> str:
    return f"sha256sum {path} | cut -d' ' -f1"


@pytest.fixture
def installers(runtime: ContainerRuntime) -> Installers:
    return Installers(runtime, (FIXTURES / "fleet.lock.yaml").read_text())


@pytest.fixture
def fresh(installers: Installers) -> Installers:
    installers.reset()
    return installers


def platform_key(runtime: ContainerRuntime) -> str:
    return "linux-arm64" if runtime.sh("uname -m").stdout.strip() == "aarch64" else "linux-x64"


def no_herdr_process(runtime: ContainerRuntime) -> None:
    """The scenarios must never leave a herdr server (or any herdr process) running."""
    count = runtime.sh("cat /proc/[0-9]*/comm 2>/dev/null | grep -cx herdr")
    assert count.stdout.strip() == "0", "a herdr process is running"


def test_a_fresh_host_reports_every_new_role_pending(fresh: Installers) -> None:
    result = fresh.check()
    assert result.returncode == 2, result.stdout + result.stderr

    assert f"herdr: expected {HERDR}, found missing" in role_messages(result, "herdr")
    eng = role_messages(result, "herdr-engineering")
    assert f"herdr-engineering revision: expected {ENG}, found missing" in eng
    assert "herdr-eng: expected the venv entry point, found missing" in eng
    pi = role_messages(result, "pi-runtime")
    assert f"pi-runtime revision: expected {PI}, found missing" in pi
    assert any(m.startswith("pi-runtime registration: ") for m in pi), pi
    assert any(m.startswith("pi-runtime dependencies: ") for m in pi), pi

    # check mode changed nothing and fetched nothing
    left = fresh.runtime.sh(
        f"test ! -e {HERDR_BIN} && test ! -e {HOME}/.local/share/herdr-engineering "
        f"&& test ! -e {HOME}/.venvs/herdr-engineering && test ! -e {SETTINGS}; echo $?"
    )
    assert left.stdout.strip() == "0", left.stdout
    no_herdr_process(fresh.runtime)


def test_b_c_apply_converges_then_is_idempotent(fresh: Installers) -> None:
    first = fresh.apply()
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["changes_made"] > 0

    after = fresh.check()
    assert after.returncode == 0, after.stdout
    for role in ("baseline", "herdr", "herdr-engineering", "pi-runtime", "toolchain"):
        assert role_messages(after, role) == [], role

    second = fresh.apply()
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)["changes_made"] == 0, second.stdout

    facts = fresh.runtime.sh(
        f"""
set -e
echo "herdr-sha $({sha_cmd(HERDR_BIN)})"
echo "herdr-mode $(stat -c %a {HERDR_BIN})"
echo "herdr-version $({HERDR_BIN} --version | head -1)"
echo "eng-version $({HOME}/.local/bin/herdr-eng --version | head -1)"
echo "fabric-mode $(stat -c %a {FABRIC_ENV})"
echo "fabric-line $(grep -c '^{FABRIC_LINE}$' {FABRIC_ENV})"
echo "unit $(test -f {UNIT} && echo yes || echo no)"
echo "gate $(cmp -s {GATE} {RELEASE_GATE} && echo same || echo differs)"
echo "eng-release $(test -d {ENG_ROOT}/releases/{ENG} && echo yes || echo no)"
echo "pi-release $(test -d {PI_ROOT}/releases/{PI}/node_modules && echo yes || echo no)"
echo "registered $(grep -c '{PI_ROOT}/releases/{PI}' {SETTINGS})"
"""
    )
    assert facts.returncode == 0, facts.stdout + facts.stderr
    got = dict(line.split(" ", 1) for line in facts.stdout.strip().splitlines())
    assert got["herdr-sha"].strip() == HERDR_SHA
    assert got["herdr-mode"].strip() == "755"
    assert got["herdr-version"].strip() == f"herdr {HERDR}"
    assert got["eng-version"].strip().startswith("herdr-eng")
    assert got["fabric-mode"].strip() == "600"
    assert got["fabric-line"].strip() == "1"
    assert got["unit"].strip() == "yes"  # rendered; no user manager here, so never started
    assert got["gate"].strip() == "same"
    assert got["eng-release"].strip() == "yes"
    assert got["pi-release"].strip() == "yes"
    assert int(got["registered"].strip()) >= 1
    no_herdr_process(fresh.runtime)


def test_d_a_herdr_binary_replaced_by_another_release_is_named_and_restored(
    installers: Installers,
) -> None:
    installers.converge()
    key = platform_key(installers.runtime)
    asset = pins.ALT_HERDR_ASSET[key]
    sha = pins.ALT_HERDR_SHA256[key]
    swap = installers.runtime.sh(
        f"""
set -e
cd {HOME}/tmp
curl -fsSL -o alt-herdr \
  https://github.com/herdrdev/herdr/releases/download/v{pins.ALT_HERDR_VERSION}/{asset}
echo "{sha}  alt-herdr" | sha256sum -c -
install -m 0755 alt-herdr {HERDR_BIN}
rm -f alt-herdr
"""
    )
    assert swap.returncode == 0, swap.stdout + swap.stderr

    drift = installers.check()
    assert drift.returncode == 2, drift.stdout
    assert role_messages(drift, "herdr") == [
        f"herdr: expected {HERDR}, found {pins.ALT_HERDR_VERSION}",
        f"herdr binary: expected sha256 {HERDR_SHA}, found {sha}",
    ]
    fixed = installers.apply()
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert installers.check().returncode == 0
    sha_now = installers.runtime.sh(sha_cmd(HERDR_BIN)).stdout.strip()
    assert sha_now == HERDR_SHA
    no_herdr_process(installers.runtime)


def test_d_a_removed_herdr_eng_venv_is_named_and_restored(installers: Installers) -> None:
    installers.converge()
    assert installers.runtime.sh(f"rm -rf {HOME}/.venvs/herdr-engineering").returncode == 0

    drift = installers.check()
    assert drift.returncode == 2, drift.stdout
    messages = role_messages(drift, "herdr-engineering")
    assert "herdr-eng: expected the venv entry point, found missing" in messages, messages

    fixed = installers.apply()
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert installers.check().returncode == 0
    version = installers.runtime.sh(f"{HOME}/.local/bin/herdr-eng --version")
    assert version.returncode == 0, version.stderr


def test_d_a_modified_build_gate_is_named_and_restored(installers: Installers) -> None:
    installers.converge()
    edit = installers.runtime.sh(f"echo '// edited by hand' >> {GATE}")
    assert edit.returncode == 0

    drift = installers.check()
    assert drift.returncode == 2, drift.stdout
    (message,) = role_messages(drift, "herdr-engineering")
    assert re.fullmatch(
        rf"build-gate: expected sha256 of release {ENG[:12]}, found [0-9a-f]{{64}}", message
    ), message

    assert installers.apply().returncode == 0
    assert installers.check().returncode == 0
    same = installers.runtime.sh(f"cmp {GATE} {RELEASE_GATE}")
    assert same.returncode == 0


def test_d_a_removed_pi_registration_is_named_restored_and_other_settings_survive(
    installers: Installers,
) -> None:
    installers.converge()
    edit = installers.runtime.sh(
        f"""
python3 - <<'PY'
import json
path = "{SETTINGS}"
settings = json.load(open(path))
settings["packages"] = [p for p in settings["packages"] if "/herdr-engineering/pi/" not in str(p)]
settings["theme"] = "example-theme"
json.dump(settings, open(path, "w"), indent=2)
PY
"""
    )
    assert edit.returncode == 0, edit.stderr

    drift = installers.check()
    assert drift.returncode == 2, drift.stdout
    # The stager's attestation includes the registration, so the revision is no longer attested.
    assert role_messages(drift, "pi-runtime") == [
        f"pi-runtime revision: expected {PI}, found missing",
        f"pi-runtime registration: expected {SETTINGS} to list {PI_ROOT}/releases/{PI}, "
        "found not registered",
    ]

    assert installers.apply().returncode == 0
    assert installers.check().returncode == 0
    settings = json.loads(installers.runtime.sh(f"cat {SETTINGS}").stdout)
    assert settings["theme"] == "example-theme", "unrelated Pi settings must survive"
    assert any(f"releases/{PI}" in str(p) for p in settings["packages"])


def test_d_a_changed_pin_stages_next_to_the_old_release_and_keeps_it(
    installers: Installers,
) -> None:
    installers.converge()
    manifest = f"{PI_ROOT}/releases/{PI}/package.json"
    before = installers.runtime.sh(sha_cmd(manifest)).stdout.strip()
    assert before

    def newer(doc: LockDoc) -> None:
        doc["repos"]["pi-engineering"] = pins.ALT_PI_ENGINEERING
        doc["repos"]["herdr-engineering"] = pins.ALT_HERDR_ENGINEERING

    alt = installers.use_lock(newer, "alt-commits.lock")
    drift = installers.check(lock=alt)
    assert drift.returncode == 2, drift.stdout
    assert f"pi-runtime revision: expected {pins.ALT_PI_ENGINEERING}, found {PI}" in role_messages(
        drift, "pi-runtime"
    )
    assert (
        f"herdr-engineering revision: expected {pins.ALT_HERDR_ENGINEERING}, found {ENG}"
        in role_messages(drift, "herdr-engineering")
    )

    applied = installers.apply(lock=alt)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert installers.check(lock=alt).returncode == 0

    state = installers.runtime.sh(
        f"""
echo "old-pi $(test -d {PI_ROOT}/releases/{PI} && echo kept || echo gone)"
echo "new-pi $(test -d {NEW_PI}/node_modules && echo yes || echo no)"
echo "old-eng $(test -d {ENG_ROOT}/releases/{ENG} && echo kept || echo gone)"
echo "new-eng $(test -d {NEW_ENG} && echo yes || echo no)"
echo "old-pi-sum $({sha_cmd(manifest)})"
echo "old-registered $(grep -c 'releases/{PI}' {SETTINGS} || true)"
echo "new-registered $(grep -c 'releases/{pins.ALT_PI_ENGINEERING}' {SETTINGS})"
"""
    )
    got = dict(line.split(" ", 1) for line in state.stdout.strip().splitlines())
    assert got["old-pi"].strip() == "kept" and got["old-eng"].strip() == "kept"
    assert got["new-pi"].strip() == "yes" and got["new-eng"].strip() == "yes"
    assert got["old-pi-sum"].strip() == before, "the older staged release must be untouched"
    assert int(got["new-registered"].strip()) >= 1
    # the helper moves the registration to the newest release (it never keeps two)
    assert int(got["old-registered"].strip()) == 0

    # and back to the original pins: the older releases are already staged, nothing is deleted
    assert installers.check().returncode == 2
    assert installers.apply().returncode == 0
    assert installers.check().returncode == 0
    after = installers.runtime.sh(f"test -d {PI_ROOT}/releases/{pins.ALT_PI_ENGINEERING}")
    assert after.returncode == 0, "the newer release must stay on disk after the rollback"


def test_f_wrong_herdr_checksum_fails_apply_before_installing(fresh: Installers) -> None:
    def break_sha(doc: LockDoc) -> None:
        doc["herdr"]["sha256"] = {key: "0" * 64 for key in pins.ALT_HERDR_SHA256}

    # earlier scenarios leave a verified download in the cache; start without one
    assert fresh.runtime.sh(f"rm -rf {HOME}/.local/share/herdr-fleet/herdr/cache").returncode == 0
    broken = fresh.use_lock(break_sha, "bad-herdr-sha.lock")
    result = fresh.apply(lock=broken)
    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(r"checksum", result.stdout + result.stderr, re.IGNORECASE), result.stdout

    state = fresh.runtime.sh(
        f"test ! -e {HERDR_BIN} && echo no-binary; "
        f"find {HOME}/.local/share/herdr-fleet/herdr/cache -type f 2>/dev/null | wc -l"
    )
    assert state.stdout.split() == ["no-binary", "0"], f"nothing may be installed: {state.stdout}"
    # apply stopped at herdr: nothing after it (baseline's empty directories aside) was staged
    staged = fresh.runtime.sh(f"test ! -e {ENG_ROOT}/releases && test ! -e {PI_ROOT}/releases")
    assert staged.returncode == 0
    no_herdr_process(fresh.runtime)
