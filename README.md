# herdr-fleet

Reproducible, updateable provisioning for a herdr-engineering fleet.

**Status: slice 2.** Schemas, loader, `plan`, the `toolchain` role, `plan --check` (drift
report) and a minimal `apply` exist. Other roles are reported as `not-implemented` and are
skipped. See
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
role, `ok`, `drift` or `failed` with the specific differences (for example `node version:
expected 22.23.2, actual 20.19.5`). Nothing on any host is changed. Exit codes: 0 no drift, 2
drift, 1 error (an unreachable host, a failed task, invalid input). `--limit HOST` restricts to
one host; `--connection local` runs against the current machine instead of ssh (used by the
container tests; the jump host and ssh options from the config are used for `ssh`).

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

### Apply

```bash
herdr-fleet apply --lock fleet.lock --config fleet-config.yaml --yes [--limit HOST] [--connection ssh|local]
```

Applies the roles one host at a time (serial 1), then re-runs the fingerprint comparison and
exits non-zero if anything still differs. Without `--yes` it asks for confirmation on a TTY and
refuses otherwise.

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

Container tests (real Node and npm downloads, so network is needed) run in Docker and in
Apptainer, never against your home directory:

```bash
pytest -m container --no-cov            # both runtimes; each skips with a reason if its binary is missing
pytest -m container --no-cov -k docker  # or -k apptainer
```

Details and the HOME-isolation rule: [docs/testing.md](docs/testing.md).

Adding a capability: [docs/adding-a-role.md](docs/adding-a-role.md).
