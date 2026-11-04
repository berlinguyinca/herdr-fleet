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
   Additive optional keys do not bump `version`, but the PR adding one must say which
   herdr-fleet release first understands it: the loader is strict, so an older herdr-fleet rejects
   a lock that uses the key ("additional property"), which is acceptable because the lock pins
   `herdr_fleet.version`. Repository clone URLs live in the lock's optional `repo_urls` (a URL
   change is a trust change) and have no built-in defaults; `herdr_fleet/repos.py:repo_url`
   fails with a message naming the key when a role needs one that is missing. A role that installs
   Python packages should take an optional hashed requirements file the same way
   (`python_requirements`, see `herdr_fleet/python_requirements.py`) and report the note
   `unpinned_python_note` while it is absent.
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
    **Pending marker.** Work that cannot run in check mode (network, `git`, `pip`, `npm`, `apt`)
    is reported with a marker task placed after the probe that decides whether work is pending:

    ```yaml
    - name: stage herdr-engineering {{ rev[:12] }} (pending)
      ansible.builtin.command:
        argv: [/bin/true]
      changed_when: true
      check_mode: false
      when: ansible_check_mode and <work is pending>
    ```

    It cannot fire in apply, so apply's `changed` counts stay honest, and the real task carries
    `when: not ansible_check_mode and <work is pending>`. `tests/test_check_marker.py` proves the
    behaviour with real `ansible-playbook`; `tests/mirror.py` is the shared local HTTP mirror
    (arbitrary files, request counting) for role tests that need a download.
10. **Container scenarios.** Add scenarios under `tests/containers/` (fresh, converged,
    idempotent, drift, failure) against real downloads. See [testing.md](testing.md).

**Privilege rule.** Roles run as the connecting user and write only under `$HOME`. `baseline` is
the only role that escalates, and only for the package-index refresh (`apt-get update`), package
installation and linger, by running `sudo -n -- <command>` through `command` (never `become`, whose
`sudo -H -S -n -u root /bin/sh -c ...` is a different command from the one probed); every probe and
check mode runs unprivileged. A new role that needs root must go through `baseline`, not add
`become`. A test scans every role for it.

**Pinning a downloaded binary.** Take the sha256 from the publisher's own metadata (for herdr, the
`digest` of each asset in
`https://api.github.com/repos/herdrdev/herdr/releases/tags/v<version>`), re-hash at least one
downloaded file, and put one digest per platform in `fleet.lock`.

Lessons from the toolchain role: pin and verify downloads (`get_url` with `checksum:
sha256:...` never extracts an unverified file); extract to a `.partial` directory and rename;
give the user a stable entry point (shim) rather than relying on the shell's PATH; and
fingerprint the user-visible outcome (`command -v` in a clean-environment bash login shell), not just the
installed files.

Run `scripts/ci.sh` before opening a PR.
