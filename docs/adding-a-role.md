# Adding a role

A role is one Ansible role that makes one capability true on a host. It is idempotent, supports
`--check`, and inherits plan, apply, verify, staggering and rollback. It never gets its own
script or timer (spec 0001, "Extending the fleet").

## Steps

1. **Pins.** Everything the role installs is pinned in `fleet.lock`: exact versions, commit
   IDs, image digests, and per-platform sha256 for downloaded binaries. Add the keys to
   `herdr_fleet/schemas/fleet-lock.schema.json` (reject ranges, branches, `latest`, short SHAs,
   tag-only images) and to `examples/fleet.lock.example.yaml`. New fields should be optional with
   safe defaults; a breaking change bumps `version` and the loader prints the migration.
2. **Role name.** Add it to the `roles` enum in `herdr_fleet/schemas/fleet-config.schema.json`
   and add a row to the spec's role table.
3. **Order.** Put it in `ROLE_ORDER` in `herdr_fleet/roles.py` after the roles it depends on.
   `plan` sorts each host's roles by that tuple.
4. **Cross-checks.** If the role needs other settings (as `ci-server` needs an ingress), add
   a rule in `herdr_fleet/config.py` with a message naming file, key and remedy.
5. **Tests.** Write failing tests first: a schema rejection per new pin, the ordering in
   `tests/test_plan.py`, and any cross-check. No mocks; use the real example files.
6. **State.** If the role holds state that cannot be rebuilt, list it under the role's
   `state:` key so backup and restore cover it.

7. **Ansible content.** The role lives in `herdr_fleet/ansible/roles/<role>/` (shipped as package
   data) and is applied by `playbooks/converge.yml`. Pins reach it only as extra-vars built in
   `herdr_fleet/inventory.py:build_extra_vars` from `fleet.lock`; never hard-code a version in
   the role. Defaults that are not pins (paths) go in `defaults/main.yml`.
8. **Fingerprint.** Report what the role makes true in `tasks/fingerprint.yml`, emit it through
   the `fleet_json` callback, model it in a typed dataclass (see `herdr_fleet/fingerprint.py`),
   and add a unit-tested `compare(fingerprint, pins)` returning specific differences. Add the
   role to `IMPLEMENTED_ROLES` in `herdr_fleet/inventory.py`; unlisted roles are reported
   `not-implemented`.
9. **Check mode.** Tasks that depend on earlier changes (an extracted Node you want to run)
   cannot run when `--check` skipped the earlier step; gate them on the thing existing, and let
   check mode report the skipped step as a pending change (the report treats "check mode"
   skips as `would_change`).
10. **Container scenarios.** Add scenarios under `tests/containers/` (fresh, converged,
    idempotent, drift, failure) against real downloads. See [testing.md](testing.md).

Lessons from the toolchain role: pin and verify downloads (`get_url` with `checksum:
sha256:...` never extracts an unverified file); extract to a `.partial` directory and rename;
give the user a stable entry point (shim) rather than relying on the shell's PATH; and
fingerprint the user-visible outcome (`command -v` in a clean-environment bash login shell), not just the
installed files.

Run `scripts/ci.sh` before opening a PR.
