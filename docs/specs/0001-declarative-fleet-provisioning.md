# 0001 — Declarative fleet provisioning and the single update path

Status: proposed

## Goal

One tool, `herdr-fleet`, makes every host in a herdr-engineering fleet match a
pinned, reviewed description of the whole system. Anyone can stand the same
fleet up from nothing, and update it, with the same commands.

## Rule: one update path

`herdr-fleet apply` is the only thing that changes software on a fleet host.

- Timers, CI and humans may *trigger* `apply`; none may change software another way.
- Per-repo update scripts, ad-hoc playbook runs and `pip`/`npm` installs on hosts
  are removed or reduced to `apply` callers once `herdr-fleet` reaches parity.
  A fallback is a second update path, so none are kept.
- Rollback is `apply` with a previous `fleet.lock`. There is no separate mechanism.

## Inputs

### `fleet.lock` (committed, reviewed)

Exact pins for everything the fleet runs. No ranges, no `main`.

| Pin | Form |
|---|---|
| Node | exact version + per-platform checksum |
| herdr | exact version + per-platform checksum |
| herdr-engineering, pi-engineering, autospec | full commit ID |
| global CLIs (Pi CLI, Codex, gitleaks, woodpecker-cli) | exact version |
| container images (Woodpecker server/agent, control plane, fabric stack) | image digest |
| herdr-fleet itself | exact version; the controller re-execs from the pin |

Changing the fleet means changing this file in a pull request. A scheduled job
may open that PR when an upstream releases; CI runs the converge test on it.

### `fleet-config.yaml` (operator-private)

Everything specific to one operator: hosts and their roles, connection details
(including jump hosts), service URLs, which repos Woodpecker serves. Validated
against a JSON Schema shipped in this repo. The repo ships only `examples/`
with generic names (`host-a`, `ci.example.com`). Real hostnames, tokens and
inventory never enter the repo.

### Secrets

Encrypted with Ansible Vault in the operator's config directory, rendered on
hosts to `0600` files or container secrets. Never written to the repo, to
inventory, or to logs. Token files carry a rotation command.

## Roles

A host lists roles in `fleet-config.yaml`; each role is one Ansible role with
its pins in `fleet.lock`. Roles are idempotent and support `--check`.

| Role | Provides |
|---|---|
| `baseline` | OS packages, state/config directories, Tailscale presence check |
| `toolchain` | Node and global CLIs in a managed prefix, independent of any user's nvm default |
| `herdr` | pinned herdr with checksum verification |
| `herdr-engineering` | the `herdr-eng` venv, fabric agent, build gate |
| `pi-runtime` | pinned pi-engineering staging (activation stays with the rollout coordinator) |
| `secret-scan` | gitleaks, global pre-commit/pre-push hooks, shared policy |
| `controller` | `herdr-fleet` itself, the update timer, the plan/apply state |
| `gateway` | control-plane and fabric-stack compose projects, as containers pinned by digest |
| `ci-server` | Woodpecker server, database, ingress |
| `ci-agent` | Woodpecker agent container(s) on a worker host |

Supported targets for v1: Debian/Ubuntu and macOS, GitHub, Tailscale. Other
platforms and forges are out of scope until the core is solid.

Services run as containers (pinned digests) or as the user's own supervised
processes. Bare system-wide service binaries are not provisioned.

## Update flow

1. **Pin.** A PR changes `fleet.lock`; CI runs lint, schema checks and the converge test.
2. **Plan.** `herdr-fleet plan` runs every role in check mode and prints the
   per-host difference between `fleet.lock` and reality. This is also the drift
   report: it needs only SSH, not the control plane.
3. **Apply.** `herdr-fleet apply` converges hosts one at a time (`serial`),
   idle hosts first. Hosts with a running mission or session receive *staged*
   artifacts and are activated when idle, using the existing activation split.
4. **Verify.** A fingerprint of each host (versions, which `node`/`pi` resolve
   first on PATH, service state) is compared with the lock. A mismatch fails
   the run and raises an attention notification; the host stays on its last
   good version.
5. **Roll back.** Re-run `apply` with the previous lock.

A lock mismatch is detected on every run, so drift cannot persist unnoticed.

## Secret scanning

Default scanner: **gitleaks** — no account, no network. `ggshield` may be added
as an optional second layer by operators who have a GitGuardian key.

- **Local:** global `core.hooksPath` pre-commit hook on every `secret-scan` host.
  A finding **blocks the commit**. The output is written for an agent harness:
  file, line, rule, and the exact remedy ("stop; remove or rotate the secret;
  recommit"). The same rule is added to the agent context rules the fleet installs.
- **CI:** the same scan is an enforced step in the shared Woodpecker pipeline template.
- **Fleet itself:** `herdr-fleet` and the operator config directory are scanned.
- A shared allowlist policy covers credential-shaped test fixtures, so fixtures
  are generated at runtime or listed explicitly rather than ignored wholesale.

## CI (Woodpecker)

`ci-server` and `ci-agent` make CI provisionable locally, on a separate host,
or against an existing server.

- **Server:** compose project with images pinned by digest. Either provisioned
  by this repo, or `ci_server.external_url` points at an existing one and only
  the declarative state below is managed.
- **Ingress:** GitHub must reach the webhook endpoint. The role supports a
  tunnel (Tailscale Funnel / cloudflared) or a public address; a tailnet-only
  server cannot receive webhooks and the plan reports that as an error.
- **Agents:** separate role per worker host; labels and capacity come from config.
  Docker-socket access is an explicit, documented privilege.
- **Declarative state:** activated repos, org secrets and runner registration
  live in Woodpecker's database, so `apply` reconciles them through the API
  idempotently ("ensure repo X is active with secrets Y"). Repair-required
  states, such as a rejected webhook token, are reported by `plan`, not hidden.
- **Pipelines:** shared templates for lint, test and secret scan ship from this repo.

## State, backup and restore

Config is reproducible by construction; state is not. State is named and
backed up as part of the fleet: the Woodpecker database, control-plane
Postgres, and anything else listed under a role's `state:` key.
`herdr-fleet backup` and `restore` are part of the tool, and a restore is
tested. Without this, "reproducible" would hold for software and fail for history.

## Bootstrap

A new operator runs one command to get a controller:
`uvx herdr-fleet init`, with Ansible pinned inside the package so no separate
install is needed. `init` writes a starter `fleet-config.yaml`, creates the
vault, enrolls the controller, and runs `plan`.

## Migration from herdr-engineering

1. Build parity here first; nothing is deleted before it works.
2. `herdr-eng fleet …` becomes a thin shim that delegates to `herdr-fleet`,
   then is deprecated.
3. Delete `ansible/` and the fleet update scripts from `herdr-engineering`
   (`fleet-self-update.sh`, `fleet-auto-update.sh`, the fleet auto-update
   timer, `fleet-sync.yml`/`site.yml`); `install.sh` shrinks to controller bootstrap.
4. Remove hard-coded names (gateway host, LLM endpoint, repo URLs) in the move.

## Testing

- `ansible-lint` and schema validation on every PR.
- A container converge test on Debian and Ubuntu: apply from nothing, then
  apply again and require `changed=0`.
- A drift test: change one pin or break one host, require `plan` to say so.
- The reproducibility proof: a fresh VM built from `fleet.lock` + config +
  restored backup reaches the same fingerprint. This is the exit gate.
- A macOS converge smoke test where a runner is available.

## Extending the fleet

- **New capability = new role.** One Ansible role, its pins in `fleet.lock`, a row in the roles
  table. It inherits plan, apply, verify, staggering and rollback; it never gets its own script or timer.
- **Compatibility.** `fleet.lock` and `fleet-config.yaml` carry a schema `version`. New fields are
  optional with safe defaults; a breaking change bumps the version and the loader prints the migration.
- **Adding a role** is documented in `docs/adding-a-role.md` once slice 1 lands.

## Slices

1. Schemas (`fleet.lock`, `fleet-config`), loader, `plan` skeleton, CI.
2. `toolchain` role + fingerprint + check-mode drift (fixes the Node/Pi-CLI drift).
3. `herdr`, `herdr-engineering`, `pi-runtime`, `baseline` roles with real installers and checksums.
4. Staggered, session-aware `apply`; verify; rollback; notifications.
5. `secret-scan` role (gitleaks, hooks, policy).
6. `ci-server` / `ci-agent` roles and declarative Woodpecker state.
7. `backup`/`restore`; reproducibility proof.
8. `init`, quickstart docs, migration shims, removal of the old paths.

## Open questions

- Ansible Vault is the default secret mechanism; revisit `sops` if multi-operator key sharing becomes painful.
- Reaching nodes that cannot run an SSH-managed agent (batch-scheduler nodes): jump host support is in the schema, but whether such nodes are in scope is undecided.
- RHEL-family support.
