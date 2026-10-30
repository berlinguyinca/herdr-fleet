# herdr-fleet

Reproducible, updateable provisioning for a herdr-engineering fleet.

**Status: slice 1.** Schemas, loader and a `plan` skeleton exist. Nothing here changes a
host yet; `apply` and the roles arrive in later slices. See
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

Both exit 0 when the inputs are valid and 1 otherwise, printing every problem with file, key
path and reason. `plan` expands hosts x roles into the ordered list of role applications
(baseline, toolchain, herdr, herdr-engineering, pi-runtime, secret-scan, controller, gateway,
ci-server, ci-agent) and states plainly that no changes were made: check-mode execution
arrives in slice 2.

Cross-checks: a `ci-agent` host needs a CI server (a host with `ci-server`, or
`services.ci_server.external_url`); `ci-server` needs `services.ci_server.ingress`
(`tunnel` or `public`).

## Development

```bash
scripts/ci.sh    # ruff check, ruff format --check, pytest with >=80% branch coverage, example validation
```

Adding a capability: [docs/adding-a-role.md](docs/adding-a-role.md).
