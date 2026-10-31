"""Spec 0001 "Testing": converge from nothing, idempotence, drift, and the original bug.

Real Node and npm downloads inside a real container; no mocks, stubs or fakes of anything.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import pytest
import yaml

from tests.containers import pins
from tests.containers.conftest import (
    HOME,
    PREFIX,
    Fleet,
    difference_fields,
    toolchain_role,
)
from tests.containers.runtime import ContainerRuntime

pytestmark = [pytest.mark.container, pytest.mark.network]

NODE = "22.23.2"
PI = "1.4.0"
CODEX = "0.166.0"


@pytest.fixture
def clean(fleet: Fleet) -> Fleet:
    fleet.reset()
    return fleet


LockDoc = dict[str, dict[str, object]]


def lock_with(fleet: Fleet, mutate: Callable[[LockDoc], object]) -> str:
    doc: LockDoc = yaml.safe_load(fleet.lock_text)
    mutate(doc)
    return yaml.safe_dump(doc)


def alt_node(doc: LockDoc) -> None:
    doc["node"]["version"] = pins.ALT_NODE_VERSION
    doc["node"]["sha256"] = pins.ALT_NODE_SHA256


def test_a_fresh_container_reports_missing_node_and_clis(clean: Fleet) -> None:
    result = clean.check()
    assert result.returncode == 2, result.stdout + result.stderr
    role = toolchain_role(result)
    assert role["status"] == "drift"
    messages = [d["message"] for d in role["differences"]]  # type: ignore[attr-defined]
    assert f"node: expected {NODE}, found missing" in messages
    assert f"cli pi-cli: expected {PI}, found missing" in messages
    assert f"cli codex: expected {CODEX}, found missing" in messages
    # check mode changed nothing
    assert clean.runtime.sh(f"test ! -e {PREFIX}/node; echo $?").stdout.strip() == "0"


def test_b_c_apply_converges_then_is_idempotent(clean: Fleet) -> None:
    first = clean.apply()
    assert first.returncode == 0, first.stdout + first.stderr
    payload = json.loads(first.stdout)
    assert payload["changes_made"] > 0 and payload["exit_code"] == 0

    after = clean.check()
    assert after.returncode == 0, after.stdout
    assert toolchain_role(after)["status"] == "ok"

    second = clean.apply()
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)["changes_made"] == 0, second.stdout

    versions = clean.runtime.sh(
        f"{PREFIX}/bin/node --version; {HOME}/.local/bin/pi --version; "
        f"{HOME}/.local/bin/codex --version"
    )
    assert versions.returncode == 0, versions.stderr
    assert f"v{NODE}" in versions.stdout and PI in versions.stdout and CODEX in versions.stdout


def test_d_node_pin_change_is_named_and_applied(clean: Fleet) -> None:
    clean.converge()
    alt = clean.use_lock(lock_with(clean, alt_node), "alt-node.lock")

    drift = clean.check(lock=alt)
    assert drift.returncode == 2, drift.stdout
    assert difference_fields(drift) == ["node"]
    messages = [d["message"] for d in toolchain_role(drift)["differences"]]  # type: ignore[attr-defined]
    assert messages == [f"node: expected {pins.ALT_NODE_VERSION}, found {NODE}"]

    assert clean.apply(lock=alt).returncode == 0
    assert clean.check(lock=alt).returncode == 0
    active = clean.runtime.sh(f"{PREFIX}/bin/node --version; ls {PREFIX}/node").stdout
    assert f"v{pins.ALT_NODE_VERSION}" in active
    assert NODE in active, "the old version may remain on disk but must not be active"
    assert clean.runtime.sh(f"{HOME}/.local/bin/pi --version").returncode == 0

    # and back to the original pin
    assert clean.check().returncode == 2
    assert clean.apply().returncode == 0
    assert clean.check().returncode == 0
    assert f"v{NODE}" in clean.runtime.sh(f"{PREFIX}/bin/node --version").stdout


def test_d_cli_pin_change_is_named_and_applied(clean: Fleet) -> None:
    clean.converge()
    older = clean.use_lock(
        lock_with(clean, lambda d: d["clis"].update({"pi-cli": pins.ALT_PI_CLI_VERSION})),
        "older-pi.lock",
    )
    drift = clean.check(lock=older)
    assert drift.returncode == 2
    assert difference_fields(drift) == ["cli pi-cli"]
    assert clean.apply(lock=older).returncode == 0
    assert clean.check(lock=older).returncode == 0
    assert pins.ALT_PI_CLI_VERSION in clean.runtime.sh(f"{HOME}/.local/bin/pi --version").stdout


def test_d_removed_pi_cli_is_named_and_restored(clean: Fleet) -> None:
    clean.converge()
    removed = clean.runtime.sh(
        f"rm -rf {PREFIX}/lib/node_modules/@earendil-works {PREFIX}/bin/pi; echo ok"
    )
    assert removed.stdout.strip() == "ok"

    drift = clean.check()
    assert drift.returncode == 2
    assert difference_fields(drift) == ["cli pi-cli"]
    assert f"cli pi-cli: expected {PI}, found missing" in json.dumps(
        toolchain_role(drift)["differences"]
    )

    assert clean.apply().returncode == 0
    assert clean.check().returncode == 0
    assert PI in clean.runtime.sh(f"{HOME}/.local/bin/pi --version").stdout


def test_d_missing_shim_is_named(clean: Fleet) -> None:
    clean.converge()
    clean.runtime.sh(f"rm -f {HOME}/.local/bin/codex")
    drift = clean.check()
    assert drift.returncode == 2
    assert "shim codex" in difference_fields(drift)
    assert clean.apply().returncode == 0
    assert clean.check().returncode == 0


REPORT_NODE_VERSION = (
    "require('fs').appendFileSync(process.env.NODE_REPORT, process.version + '\\n');"
)


def install_decoy_nvm(runtime: ContainerRuntime) -> str:
    """A fake-nvm layout whose default Node is the real Node 20, first on PATH like nvm does."""
    arch = runtime.sh("uname -m").stdout.strip()
    key = "linux-arm64" if arch == "aarch64" else "linux-x64"
    version, sha = pins.DECOY_NODE_VERSION, pins.DECOY_NODE_SHA256[key]
    archive = f"node-v{version}-{key}.tar.xz"
    nvm_node = f"{HOME}/.nvm/versions/node/v{version}"
    script = f"""
set -e
mkdir -p {nvm_node} {HOME}/decoy
cd {HOME}/decoy
curl -fsSL -o {archive} https://nodejs.org/dist/v{version}/{archive}
echo "{sha}  {archive}" | sha256sum -c -
tar -xJf {archive} -C {nvm_node} --strip-components=1
printf '%s\\n' "{REPORT_NODE_VERSION}" > {HOME}/decoy/report.js
"""
    done = runtime.sh(script)
    assert done.returncode == 0, done.stdout + done.stderr
    return f"{nvm_node}/bin"


def run_with_decoy_first(
    runtime: ContainerRuntime, nvm_bin: str, command: str
) -> tuple[int, str, str]:
    """Run ``command`` with the decoy Node first on PATH and record which Node actually ran."""
    report = f"{HOME}/decoy/node-used.txt"
    script = f"""
rm -f {report}
export PATH={nvm_bin}:{HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
export NODE_REPORT={report} NODE_OPTIONS="--require={HOME}/decoy/report.js"
{command}
rc=$?
echo "---used---"
cat {report} 2>/dev/null
exit $rc
"""
    done = runtime.sh(script)
    out, _, used = done.stdout.partition("---used---")
    return done.returncode, out, used


def test_e_original_bug_shim_uses_managed_node_despite_nvm_default(clean: Fleet) -> None:
    clean.converge()
    nvm_bin = install_decoy_nvm(clean.runtime)
    managed = f"v{NODE}"
    decoy = f"v{pins.DECOY_NODE_VERSION}"

    # the shell's default `node` is now the decoy Node 20, as after an nvm default change
    default_node = clean.runtime.sh(f"PATH={nvm_bin}:/usr/bin:/bin node --version").stdout.strip()
    assert default_node == decoy

    # with the shim: the managed Node runs the CLI, and it works
    rc, out, used = run_with_decoy_first(clean.runtime, nvm_bin, "pi --version")
    assert rc == 0, out
    assert PI in out
    versions = set(used.split())
    assert versions == {managed}, f"shim ran node {versions}, expected only {managed}"

    # without the shim logic: the very same CLI entry point resolves `env node` to the decoy
    rc_raw, out_raw, used_raw = run_with_decoy_first(
        clean.runtime,
        nvm_bin,
        f"{PREFIX}/lib/node_modules/.bin/pi --version" if False else f"{PREFIX}/bin/pi --version",
    )
    assert decoy in set(used_raw.split()), "without the shim the CLI must pick up the decoy Node"
    assert managed not in set(used_raw.split())
    print(f"[bug-repro] without shim: rc={rc_raw}, node used={used_raw.split()}")


def test_e_a_shadowing_pi_earlier_on_the_login_path_is_reported(clean: Fleet) -> None:
    clean.converge()
    clean.runtime.sh(
        f"mkdir -p {HOME}/decoy/bin && printf '#!/bin/sh\\necho decoy\\n' > {HOME}/decoy/bin/pi "
        f"&& chmod +x {HOME}/decoy/bin/pi "
        f"&& echo 'export PATH=\"{HOME}/decoy/bin:$PATH\"' >> {HOME}/.profile"
    )
    drift = clean.check()
    assert drift.returncode == 2, drift.stdout
    assert difference_fields(drift) == ["path pi-cli"]
    assert f"{HOME}/decoy/bin/pi" in drift.stdout
    # apply cannot fix a shadowing PATH entry: it must fail verification loudly
    assert clean.apply().returncode == 1


def test_f_checksum_mismatch_fails_before_extracting_anything(clean: Fleet) -> None:
    def break_sha(doc: LockDoc) -> None:
        doc["node"]["sha256"] = {platform: "0" * 64 for platform in pins.ALT_NODE_SHA256}

    broken = clean.use_lock(lock_with(clean, break_sha), "bad-sha.lock")
    result = clean.apply(lock=broken)
    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(r"checksum", result.stdout, re.IGNORECASE), result.stdout

    state = clean.runtime.sh(
        f"ls {PREFIX}/node 2>/dev/null | wc -l; ls {PREFIX}/bin 2>/dev/null | wc -l; "
        f"ls {HOME}/.local/bin 2>/dev/null | wc -l"
    )
    assert state.stdout.split() == ["0", "0", "0"], f"nothing may be installed: {state.stdout}"
