# herdr-fleet

Reproducible, updateable provisioning for a herdr-engineering fleet.

**Status: slice 3.** Schemas, loader, `plan`, the `baseline`, `toolchain`, `herdr`,
`herdr-engineering` and `pi-runtime` roles, `plan --check` (drift report) and a minimal `apply`
exist. Other roles are reported as `not-implemented` and are skipped. The design is in
[docs/specs/0001-declarative-fleet-provisioning.md](docs/specs/0001-declarative-fleet-provisioning.md).

## Install

Python 3.12 or newer.

```bash
uv venv && uv pip install -e '.[dev]'     # or: pip install -e '.[dev]'
```

## Inputs

- `fleet.lock`: exact pins (commit IDs, exact versions, image digests). Committed and reviewed.
- `fleet-config.yaml`: your hosts, roles and service URLs. Keep it outside this repo.

Generic starting points: `examples/fleet.lock.example.yaml` and
`examples/fleet-config.example.yaml`. Both carry `version: 1`; any other version is rejected
with the supported version named. Schemas live in `herdr_fleet/schemas/`.

## Usage

```bash
herdr-fleet validate --lock fleet.lock --config fleet-config.yaml [--json]
herdr-fleet plan     --lock fleet.lock --config fleet-config.yaml [--json]
```

`validate` and plain `plan` exit 0 when the inputs are valid and 1 otherwise, printing every
problem with file, key path and reason. Plain `plan` expands hosts x roles into the ordered list
of role applications and changes nothing.

### Drift report: `plan --check`

```bash
herdr-fleet plan --check --lock fleet.lock --config fleet-config.yaml [--json] [--limit HOST] [--connection ssh|local]
```

Runs the packaged Ansible playbook in check mode (`--check --diff`) and prints, per host and per
role, `ok`, `drift`, `failed` or `not attempted` with the specific differences (for example
`node: expected 22.23.2, found 20.19.5`). Nothing on any host is changed. Exit codes: 0 no drift, 2
drift, 1 error (an unreachable host, a failed task, invalid input). `--limit HOST` restricts to
one host; `--connection local` runs against the current machine instead of ssh (used by the
container tests). For `ssh`, each host's `connection.jump_host` names another host in the file
and becomes `ssh -J user@address`. Only one hop is supported: a jump host that itself has a
`jump_host` is rejected. `address` must be a hostname, IPv4 or bracketed IPv6 address and `user`
must match `^[a-z_][a-z0-9._-]*$` (nothing starting with `-`), because both end up on an ssh
command line.

`plan --check` never touches the network: when Node is absent it reports the install as a
pending change without downloading the archive, so a wrong `sha256` in `fleet.lock` or an
unreachable mirror only surfaces on `apply` (the download is checksum-verified there).

What "never touches the network" and "changes nothing" mean exactly: check mode downloads, clones
and installs nothing and writes no managed file, directory or setting on the host. It does run
read-only probes on the host: file reads and hashes, `--version` of installed binaries, `dpkg-query`,
`systemctl --user show`, and for the staged releases the local `git rev-parse HEAD` and
`git status --porcelain --untracked-files=all` (run with `GIT_OPTIONAL_LOCKS=0` so they take no
`index.lock`; a test shows the index file is byte-identical afterwards) plus
`stage_release.py --describe`, which is offline. A pending change is reported from those reads
alone.

Each host also reports a fingerprint (os/arch, managed Node version, CLI versions, shim path,
what `command -v pi` resolves to in a clean-environment bash login shell (`bash -lc`, no TTY;
only a marker line of its output is read, so profile banners cannot cause drift), and whether
that is the shim).

### Toolchain role

Per user, no root. It installs the exact Node from `fleet.lock` (sha256 verified before
extraction) under `~/.local/share/herdr-fleet/toolchain/node/<version>/`, points
`toolchain/bin/{node,npm,npx}` at it, installs the pinned `pi` and `codex` npm packages into
that prefix, and writes POSIX `sh` shims `~/.local/bin/pi` and `~/.local/bin/codex` that put the
managed bin first on `PATH` and then exec the real CLI. Changing the shell's default Node
(nvm, etc.) therefore cannot make `pi` vanish. `node` itself is deliberately not shimmed.
`gitleaks` and `woodpecker-cli` are out of scope for this slice.

### Other roles (slice 3)

- **`herdr`** (no root): downloads the pinned herdr release asset, verifies its sha256 from
  `fleet.lock` (`herdr.sha256.<platform>`, the `digest` of each GitHub release asset) before
  installing, and installs a real file `~/.local/bin/herdr` (0755). A wrong checksum fails before
  anything is installed. It never starts, restarts or updates a running herdr server; a stale
  server is reported informationally only.
- **`herdr-engineering`** (no root): stages the pinned commit with the vendored release stager
  into a write-once release directory, builds the `herdr-eng` venv, links `~/.local/bin/herdr-eng`,
  writes `~/.config/herdr-engineering/fabric.env` (0600, from `services.gateway.url`, plus
  `services.control_plane.url` only when set), renders the fabric agent unit and installs the Pi
  build gate. The service is only managed when a systemd user manager exists. Needs Python 3.12 or
  newer (`python3` on the target's `PATH`).
- **`pi-runtime`** (no root): **staging only**. Puts the pinned pi-engineering commit on disk,
  installs its dependencies with the managed toolchain's `npm`, and registers the release in
  `~/.pi/agent/settings.json` (other settings are preserved). It never reloads, restarts or
  signals Pi, never touches missions and never deletes an older release; activation belongs to
  the rollout coordinator. Rollout revisions must equal `repos.pi-engineering`.
- **`baseline`**: required packages (`git curl ca-certificates python3 python3-venv xz-utils`) plus
  `baseline.extra_packages` from the config (Debian and Ubuntu only: on macOS the role installs
  nothing, so `extra_packages` are ignored there), the state/config directories, linger and an
  informational Tailscale probe. **It is the only role that may use root**, and only for package
  installation and enabling linger. Probes and `plan --check` never need root or a password. When
  root is needed and unavailable, apply fails with the exact command to run once, for example
  `sudo apt-get install -y jq`.

`herdr-engineering` needs `services.gateway.url` and the `herdr` role on the same host;
`pi-runtime` needs `toolchain` on the same host.

Repository clone URLs are pinned in the lock's optional `repo_urls` (`herdr-engineering`,
`pi-engineering`, `autospec`; HTTPS only). They have no built-in defaults: a URL change is a trust
change, so a role that clones a repository fails naming the missing key.

Python dependencies of `herdr-engineering` can be pinned with the lock's optional
`python_requirements.herdr-engineering` (`file`: a hashed requirements file shipped next to the
lock; `sha256`: its checksum). The stager and the role then run `pip install --require-hashes -r`
and `pip install --no-deps`, the fingerprint reports the checksum and `plan --check` names a
mismatch. Without the field nothing changes and `plan` prints the note "unpinned python
dependencies". First understood by herdr-fleet 0.1.0. See
`docs/follow-ups/python-dependencies-are-not-pinned.md` for what remains.

Not automated: systemd user units and linger (no user manager in the test containers) and macOS.
Run `plan --check` and `apply` once on a real Debian host and a real Mac as a manual canary before
rolling a lock to the fleet.

### Apply

```bash
herdr-fleet apply --lock fleet.lock --config fleet-config.yaml --yes [--limit HOST] [--connection ssh|local]
```

Applies the roles one host at a time and verifies each host (re-runs the fingerprint comparison)
right after applying it. It stops at the first host that fails or does not verify; later hosts
are reported as `not attempted`. Exit is non-zero if anything fails or still differs. Without
`--yes` it lists the hosts it will converge and asks on a TTY (the prompt goes to stderr, so
`--json` output on stdout stays valid), and refuses otherwise. `--connection local` runs the
playbook on this machine for whichever hosts are selected, so `apply` requires `--limit HOST`
with it.

Not implemented yet (slice 4): staggering by host activity, session-awareness, rollback and
notifications. Do not use `apply` as a fleet-wide rollout tool until they exist.

Cross-checks: a `ci-agent` host needs a CI server (a host with `ci-server`, or
`services.ci_server.external_url`); `ci-server` needs `services.ci_server.ingress`
(`tunnel` or `public`). A `ci-server` host and `external_url` are mutually exclusive:
either provision a server here or point at an existing one.

## Development

```bash
scripts/ci.sh    # ruff, pytest with >=80% branch coverage, example validation, container tests if docker exists
```

Container tests (real Node, npm and herdr downloads and clones of public repositories, so network is needed) run in Docker and in
Apptainer, never against your home directory:

```bash
pytest -m container --no-cov            # both runtimes; each skips with a reason if its binary is missing
pytest -m container --no-cov -k docker  # or -k apptainer
```

Details and the HOME-isolation rule: [docs/testing.md](docs/testing.md).

Adding a capability: [docs/adding-a-role.md](docs/adding-a-role.md).
