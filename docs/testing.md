# Testing

## Layers

- Unit and in-process CLI tests (`pytest`): no containers, no network. Real files, real
  `ansible-playbook` in check mode with a throwaway `HOME`. Coverage gate: 80% branch.
- Container tests (`pytest -m container --no-cov`): marked `container` and `network`. They are
  excluded from the default run (`-m 'not container'` in `pyproject.toml`).

## Container harness (`tests/containers/`)

`Dockerfile` is debian:bookworm-slim with python3, venv, curl, ca-certificates, xz-utils, git and
a non-root user `fleet`. There is no sudo. `uv` provides Python 3.12 inside the test home
because ansible-core 2.21 needs 3.12 and bookworm ships 3.11.

`runtime.py` has one abstraction with two real implementations:

- Docker: builds image `herdr-fleet-test-img-<id>`, starts container `herdr-fleet-test-<id>`
  (`--cap-drop ALL`, `no-new-privileges`), mounts the repo read-only at `/repo`.
- Apptainer: builds a SIF from `docker-daemon://` into a temp work directory, runs with
  `--containall`, a temp directory as `/home/fleet`, and the repo bound read-only.

Both are real; nothing is mocked. If a runtime binary is missing its tests skip with an explicit
reason. Everything created is named `herdr-fleet-test-*` and teardown removes only those objects.
Never run `docker system prune` or any other prune to clean up after the tests.

## HOME-isolation rule

The harness never bind-mounts or exposes the real host `$HOME` or any user config directory
(`.ssh`, `.claude`, `.aws`, `.gnupg`, `.npmrc`, `.gitconfig`). Docker mounts only the repo
(read-only) and container-local storage. Apptainer always uses `--containall` with its own
temp home. `tests/containers/test_isolation.py` asserts this (non-root, no sudo, repo read-only,
host HOME not among the container mount points, no config dirs in the test home). In-process
tests that run the real toolchain role must set `HOME` to a temporary directory first.

## Scenarios

fresh host reports missing node and CLIs (exit 2); apply then check exits 0; a second apply makes
zero changes; drift (changed Node pin, changed CLI pin, removed `pi`, missing shim) is named and
fixed by apply; the original bug (a decoy Node of another major earlier on `PATH`, fake nvm) is
reproduced and the shim still runs the managed Node; a wrong sha256 fails apply before
extraction and installs nothing. The fixture lock uses real sha256 sums for Node 22.23.2.

### Installer scenarios (`test_installer_scenarios.py`, slice 3)

One host with `baseline, toolchain, herdr, herdr-engineering, pi-runtime`, using the fixture lock's
real pins: herdr 0.9.1 (the `digest` of each GitHub release asset; linux-x64 also re-hashed from the
downloaded file), and full commit ids of the public herdr-engineering and pi-engineering
repositories cloned over HTTPS from `repo_urls`. Scenarios: a fresh host reports every new role
pending (exit 2) and changes nothing; apply converges, check exits 0, a second apply makes zero
changes (herdr sha256/mode/version, `fabric.env` 0600, rendered unit, build gate, Pi release and
registration verified); a herdr binary replaced by a different real release (0.9.2), a removed
`herdr-eng` venv, a modified build gate and a removed Pi registration (other Pi settings survive)
are named with the exact messages and fixed by apply; changed herdr-engineering and
pi-engineering commits stage next to the old releases, which stay untouched, and switching back
works; a wrong herdr sha256 fails apply before installing anything. Every scenario asserts that
no herdr process was started: the real binary is only asked for `--version` and `status`, inside the
container, never against a host socket.

`test_baseline_scenario.py` runs real `apt` as container root with only the capabilities apt needs
(`DockerRuntime(extra_caps=...)`, never `--privileged`); it is Docker-only and skips on Apptainer.

Known fixture dependencies: both public repositories and the herdr GitHub release must stay
reachable, and the scenarios need PyPI and the npm registry (`pip install`/`npm ci` of the staged
releases). To re-pin herdr: `curl -sL https://api.github.com/repos/herdrdev/herdr/releases/tags/v<ver>`
and read each asset's `digest`.

### Not covered by automated tests (manual canary)

systemd user units, enable/start of the fabric agent and linger (the containers have no user
manager, so the fingerprint says `unmanaged`; the unit text, `fabric.env` and the probes are
covered), and macOS paths (launchd, Homebrew probes). Their logic is covered by rendering and unit
tests. Before a lock is rolled to the fleet, run `herdr-fleet plan --check` then `apply` once on
one real Debian host with a systemd user manager and once on a real Mac and confirm the second
`plan --check` exits 0.

## Test doubles that remain, and what they are

The rule is no mocks, stubs or fakes. These are the exceptions, stated precisely.

Replaced by the real thing (slice 3 review):

- `npm` in `tests/test_stage_release.py` and `tests/test_pi_runtime_apply.py` is the host's real
  `npm` and `node` (symlinked into the managed toolchain `bin` for the role test, no wrapper). It
  runs a real `npm ci` on a tiny package (`tests/real_npm.py`) whose only dependency is a `file:`
  directory in the same repository and whose `package-lock.json` was written by real npm, so no
  registry is needed. A real `prepare` lifecycle script logs the `PATH` npm ran with. npm's HOME and
  cache are throwaway. Without node and npm on `PATH` these tests skip.

Still doubles, because the real thing needs the internet or cannot run on the host:

- `FAKE_HERDR` in `tests/test_herdr_apply.py` is a shell script with canned answers to `--version`
  and `status`, served by a local HTTP mirror. It is a stub: it proves the role's download, sha256
  check, install, mode, symlink handling and drift messages, not that the real herdr runs. The real
  release binary is exercised only in `tests/containers/test_installer_scenarios.py`, never on the
  host.
- `FAKE_NODE` and `FAKE_NPM` in `tests/test_toolchain_apply.py` are canned scripts inside a node
  tarball built on the fly: the toolchain role downloads Node and runs `npm install --global
  <pkg>@<version>` from a registry, which cannot be done offline. They prove the role's
  verification, extraction, shims and idempotence. The real Node 22.23.2 and real global CLIs are
  covered by the container scenarios.
- The `#!/bin/sh` one-liners in `tests/test_fingerprint_probe.py` and
  `tests/test_cli_check_inprocess.py` are fixtures standing in for binaries on `PATH` whose only
  observable behaviour is their exit status or a version line.
