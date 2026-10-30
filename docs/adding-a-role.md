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

Run `scripts/ci.sh` before opening a PR.
