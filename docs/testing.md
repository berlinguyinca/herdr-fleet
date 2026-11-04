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

## Test doubles

The rule is no mocks, stubs or fakes. After the slice-3 review follow-up there are no stand-ins for
Node, npm, herdr, the herdr server or pip left in the host-side tests.

Real artifacts (`tests/artifacts.py`): the pinned Node.js tarball from nodejs.org and the pinned
herdr release binary from GitHub. Each is downloaded once into `$HERDR_FLEET_TEST_CACHE` (default
`.test-cache/` in the repository, git-ignored, never the real home) and its sha256 is re-checked on
every use, against the pins in `tests/containers/fixtures/fleet.lock.yaml`. Without network and
without a cached copy these tests skip with the URL in the reason; they never fall back to a fake.

- `tests/test_herdr_apply.py` serves the real herdr binary from the local HTTP mirror and runs it
  (`--version`, `status`) for real. herdr resolves its socket from the account's real home, not from
  `$HOME`, so `isolate_herdr` points `HERDR_SOCKET_PATH`, `HERDR_CLIENT_SOCKET_PATH` and the XDG
  directories at a throwaway directory; the binary reports "server not running" and never reaches
  the operator's server. No herdr process is started.
- `tests/test_toolchain_apply.py` serves the real Node tarball plus a local npm registry
  (`tests/local_registry.py`: real packument JSON, real `.tgz` packages with sha512 integrity) and the
  role runs a real `npm install --global` against it (`npm_config_registry`, throwaway npm cache).
  `tests/test_cli_check_inprocess.py` prepares its prefix the same way (real Node extracted, real
  `npm install --global`).
- `npm` in `tests/test_stage_release.py` and `tests/test_pi_runtime_apply.py` is the host's real
  `npm` and `node` running a real `npm ci` on a tiny package (`tests/real_npm.py`) whose only
  dependency is a `file:` directory. Without node and npm on `PATH` these tests skip.
- pip in `tests/test_python_requirements*.py` is the real pip, installing real wheels from a local
  wheel directory and index (`tests/wheels.py`) with `--require-hashes`.
- The one-line "binaries" in `tests/test_fingerprint_probe.py` and `tests/test_baseline_apply.py`
  are symlinks to the real `/usr/bin/true` and `/usr/bin/false`.

What is still not a real counterpart, stated precisely:

- A running herdr server. Starting one would spawn a real terminal multiplexer process, so the
  host tests never do; the "running server older than the binary" case is covered by parsing tests
  (`tests/test_herdr.py`) and not by a live server. The container scenarios assert that no herdr
  process was started either.
- The local npm registry is a small HTTP server in the test code, serving real package tarballs: it
  is the registry that is ours, npm is the real client.
- Files such as `#!/bin/sh` decoys written by a few tests (a pre-existing foreign `herdr`, a decoy
  `pi` on `PATH` in the container scenario) are fixtures describing what is already on the host,
  and are expected to be replaced or ignored by the role, not stand-ins for a dependency.
- Wheels are built with the standard library (`tests/wheels.py`) rather than downloaded; pip build
  isolation still fetches build-backend requirements of the release directory unhashed (documented
  in `docs/follow-ups/python-dependencies-are-not-pinned.md`).

### systemd user units and linger

Not automated, and the reason is verified, not assumed. A systemd-capable container (systemd as
PID 1 for `systemctl --user`) was tried with Docker 29 on cgroup v2: systemd exits with status 255
immediately unless the container is `--privileged` or bind-mounts the host's `/sys/fs/cgroup`
read-write, even with `--cgroupns private`, `SYS_ADMIN`, `seccomp=unconfined`,
`apparmor=unconfined` and `systempaths=unconfined`. The harness rules forbid `--privileged` and do
not expose host state to a container, so the path stays a manual canary. Apptainer cannot run
systemd as PID 1 unprivileged. What is covered: the rendered unit text, `fabric.env` (0600), the
probes and the `unmanaged` fingerprint without a user manager, and `systemd-analyze --user verify`
of the rendered unit on the host (`test_the_rendered_unit_is_accepted_by_systemd_analyze`).
macOS paths (launchd, Homebrew probes) are likewise manual.
