# 0002 - Slice 3: `herdr`, `herdr-engineering`, `pi-runtime`, `baseline` installers

Status: implemented (T0-T10) on `feat/slice-3-installers`, pending the manual canary (section 7: systemd user units, linger, macOS). Implementation plan for spec 0001, slice 3.
Branch: `feat/slice-3-installers`

Citations: `HE:` means the read-only checkout of herdr-engineering (`ansible/...`, `scripts/...`,
`lock/...`, `docs/...` relative to its root). `HF:` means this repo. Anything not cited is a design
decision of this plan, not a fact about herdr-engineering. Items needing the operator are marked
**OPEN QUESTION** with a recommended default; executors take the default unless told otherwise.

## 1. Facts this plan rests on

Existing herdr-engineering behaviour (what we must reach parity with, then replace):

- Linux/macOS roles install packages (`vim mc btop python3 python3-pip python3-venv git curl` on
  Debian) with root, and their herdr and Pi "installs" are placeholders that only print
  "install required" (`HE:ansible/roles/herdr_linux/tasks/main.yml`, `herdr_macos/tasks/main.yml`).
  They also clone `pi-engineering` and `autospec` at `version: main` with `ignore_errors: true`
  (same files). That violates the lock rule and is not ported (see section 9).
- `HE:ansible/files/stage-release.py` stages exact commits. Write-once release directories
  `~/.local/share/herdr-engineering/{herdr,pi}/releases/<sha>`, receipts under `receipts/<sha>.json`,
  `installed.json`, an `fcntl` stage lock, fetch by `git fetch --depth 1 origin <sha>`, refusal on a
  dirty or wrong-commit checkout. Kind `herdr`: creates `~/.venvs/herdr-engineering` and runs
  `pip install --upgrade <release dir>`, then checks PEP 610 `direct_url.json` matches the release.
  Kind `pi`: `npm ci --include=dev` in the release dir, then registers the release path in
  `~/.pi/agent/settings.json` `packages` under the `settings.json.lock` mkdir lock. `--describe`
  re-verifies everything offline and prints `{revision, sourceRoot}` or exits 1. Repo URLs are
  hard-coded in `REPOSITORIES`.
- Fabric agent: `HE:ansible/tasks/fabric-agent.yml` writes `~/.config/herdr-engineering/fabric.env`
  (0600, `HERDR_ENGINEERING_FABRIC_URL=...`), runs the release's `scripts/fabric-agent.sh` (Linux)
  or `scripts/fabric-agent-macos.sh --repo` (macOS). `fabric-agent.sh` renders
  `scripts/herdr-eng-fabric-agent.service` (placeholder `__HERDR_ENG__`) into
  `~/.config/systemd/user/`, runs `loginctl enable-linger` through `sudo`, restarts the unit.
  `HE:ansible/tasks/fabric-systemd.yml` additionally installs an enroll-sync watcher unit and
  enables lingering with `become: true`. `HE:ansible/tasks/build-cleanup.yml` installs a third
  companion unit.
- Build gate: `HE:ansible/tasks/pi-build-gate.yml` copies the release's
  `deploy/pi-build-gate/build-gate.ts` to `~/.pi/agent/extensions/build-gate.ts` (0644).
- Fingerprint: `HE:ansible/tasks/fleet-fingerprint.yml` reports `herdr-eng --version`, an md5 of
  `build-gate.ts`, fabric agent `active|inactive`, and the `--describe` attestation for the herdr and
  pi kinds.
- Pi staging versus activation: `HE:ansible/playbooks/pi-runtime-stage.yml` says "Package staging
  only. Activation and mission recovery require exact session control operations from the durable
  fleet rollout coordinator." `HE:herdr_engineering/fleet.py:stage_pi_runtime` is what the
  coordinator calls (`HE:herdr_engineering/fleet_rollout.py:apply_rollout`, `stage=...`), and
  `HE:docs/operations/fleet-runtime-rollout.md` defines plan, apply, run, activation and recovery as
  the coordinator's job. "A rollback is a separately reviewed pinned release plan."
- Python: herdr-engineering needs `requires-python >= 3.12` and depends on `PyYAML asyncpg boto3`
  (`HE:pyproject.toml`). Debian 12 ships 3.11 (see `HF:docs/testing.md`, which provisions 3.12 via
  `uv` for the harness).
- Pin placeholders: `HE:lock/upstreams.yaml` records herdr as `revision: "0.9.1"` with no checksum
  and says the install is the "official installer". Its comment "Replace with the official
  installer" is the TODO this slice closes.

Herdr release metadata (public, read with curl 2026-10-30):

- `https://herdr.dev/install.sh` downloads `https://herdr.dev/latest.json`, picks
  `assets["<os>-<arch>"]` and `sha256["<os>-<arch>"]`, downloads the raw binary (not an archive),
  verifies sha256, writes `~/.local/bin/herdr`.
- Asset names: `herdr-linux-x86_64`, `herdr-linux-aarch64`, `herdr-macos-x86_64`,
  `herdr-macos-aarch64` (and a Windows zip, out of scope), at
  `https://github.com/herdrdev/herdr/releases/download/v<version>/<asset>`. The URL for a pinned old
  version (0.9.1) returns HTTP 200. `latest.json` carries checksums only for the newest version
  (0.9.5 today); for older tags the GitHub release API exposes a per-asset `digest: sha256:...`.
  The 0.9.1 `herdr-linux-x86_64` digest (`2a02fed1...54b7`) equals `sha256sum` of the binary
  installed on the planning workstation, so API digests are a valid pin source. There is no
  `SHA256SUMS` file on the release.
- `herdr update` ("Download and install the latest version") and a startup "auto-update check"
  (log text: "available, waiting for explicit install") exist. A human running `herdr update` is a
  second update path; the fingerprint must catch it. `herdr status` prints client and server
  `version:`; a running server keeps its old binary until restarted or handed off.

Existing herdr-fleet model (HF):

- Roles are Ansible roles under `herdr_fleet/ansible/roles/`, run by `playbooks/converge.yml`
  (serial 1, tags: no filter = plan `--check`, `--skip-tags fingerprint` = apply, `--tags fingerprint`
  = verify). Only `toolchain` is wired in; `IMPLEMENTED_ROLES` in `herdr_fleet/inventory.py` gates it.
- Pins reach roles only as extra-vars from `build_extra_vars(lock)`; `toolchain_pins` /
  `ToolchainPins` / `CLI_SPECS` live in `herdr_fleet/toolchain.py`.
- The fingerprint is toolchain-shaped: `Fingerprint.from_json` requires `clis`; `compare(fp,
  ToolchainPins)` is called by `report._host_report` only when `role == "toolchain"`; `cli.py` passes
  `toolchain_pins(lock)` to `build_reports`. The `fleet_json` callback emits a fingerprint event for
  any task whose facts hold `fleet_fingerprint`; `parse_events` keeps the last event per host.
- Check-mode precedent (`roles/toolchain/tasks/main.yml`): downloads and extraction are
  `when: not ansible_check_mode`; the pending install is reported by the "move into place" step;
  steps needing artifacts that check mode could not create are gated by `toolchain_can_run`; the
  callback turns "check mode" skips into `would_change`; fingerprint probes use `check_mode: false`
  and are read-only.
- Lock schema (`herdr_fleet/schemas/fleet-lock.schema.json`) already has `herdr` as
  `{version, sha256{linux-x64|linux-arm64|darwin-x64|darwin-arm64}}`, `repos.{herdr-engineering,
  pi-engineering, autospec}` as full commit IDs, strict `additionalProperties: false`, `version: 1`.
  Config schema already has `services.gateway.url` and the four role names.
- Test layers: unit; real `ansible-playbook` in-process with a local HTTP mirror serving a fake Node
  (`tests/test_toolchain_apply.py`, throwaway `HOME`); container scenarios on Docker and Apptainer
  (`tests/containers/`, non-root, no sudo, `--cap-drop ALL`, repo read-only, `fleet` user, real
  downloads, fixture `tests/containers/fixtures/fleet.lock.yaml`).

## 2. Cross-cutting design

### 2.1 Lock and config schema changes (all additive, `version` stays 1)

`fleet.lock`:

1. `herdr`: no schema change. Version and per-platform sha256 are already required. `herdr_fleet/
   herdr.py` maps lock platform keys to herdr asset names (`linux-x64 -> herdr-linux-x86_64`,
   `linux-arm64 -> herdr-linux-aarch64`, `darwin-x64 -> herdr-macos-x86_64`, `darwin-arm64 ->
   herdr-macos-aarch64`) and passes the resolved table as an extra-var, so the role never maps names.
   The URL base is a role default (`herdr_release_base_url`, like `toolchain_node_dist_url`), not a pin.
2. New optional `repo_urls` object: keys `herdr-engineering`, `pi-engineering`, `autospec`; values
   `^https://\S+\.git$`-style strings. Default (applied in `herdr_fleet/repos.py`, not in the schema)
   is the three GitHub URLs hard-coded today in `HE:ansible/files/stage-release.py:REPOSITORIES`
   (herdr-engineering and pi-engineering; autospec URL from `HE:ansible/roles/herdr_linux/tasks/main.yml`).
   A URL change is a trust change, so it belongs in the reviewed lock, not the operator-private config.
3. `repos.autospec` stays required and unused (see 9).
4. Compatibility handling: loader is strict, so a lock using `repo_urls` fails on an older
   herdr-fleet with "additional property" (an acceptable, loud failure, because the lock pins
   `herdr_fleet.version` and the controller re-execs from the pin). Rule to document in
   `docs/adding-a-role.md`: additive optional keys do not bump `version`; the PR adding one must say
   which herdr-fleet release first understands it. Keep `tests/test_lock_schema.py` rejecting `latest`,
   ranges, short SHAs for the new keys.

`fleet-config.yaml`:

1. New optional top-level `baseline: {extra_packages: [str]}` (default empty): operator conveniences
   such as the three editors/monitors the old role installs. Package-name pattern `^[a-z0-9][a-z0-9+.-]*$`.
2. New optional `services.control_plane.url` (same pattern as `gateway.url`) **OPEN QUESTION 1**.
3. Cross-checks in `herdr_fleet/config.py` (messages name file, key, remedy):
   `herdr-engineering` on a host requires `services.gateway.url` and the `herdr` role on that host;
   `pi-runtime` requires `toolchain` on that host (it needs the managed Node/npm);
   `herdr-engineering` requires `toolchain`? No: it needs Python, not Node, so no.
4. `examples/fleet.lock.example.yaml` and `examples/fleet-config.example.yaml` gain the new optional
   keys with generic values (`host-a`, `example.com`).

### 2.2 Pins module and fingerprint generalisation

- New `herdr_fleet/pins.py`: `FleetPins` dataclass bundling `ToolchainPins`, `HerdrPins`,
  `HerdrEngineeringPins`, `PiRuntimePins`, `BaselinePins`; `fleet_pins(lock, config)`. Per-role
  modules (`herdr.py`, `herdr_engineering.py`, `pi_runtime.py`, `baseline.py`) hold the pins
  dataclass, the typed fingerprint dataclass and its `compare`, mirroring `toolchain.py` +
  `fingerprint.py`. `build_extra_vars(lock, config)` merges each role's vars; `cli.py` and
  `report.py` take `FleetPins` instead of `ToolchainPins`.
- `Fingerprint` keeps its current JSON keys (system, machine, node_version, clis) so existing tests
  and container assertions pass, and gains optional sections `herdr`, `herdr_engineering`,
  `pi_runtime`, `baseline`. A section is absent when the role is not on the host; `clis` and
  `node_version` become optional with empty defaults when `toolchain` is not a host role.
- `report._host_report` replaces the `role == "toolchain"` special case with a registry
  `ROLE_COMPARATORS: dict[str, Callable[[Fingerprint, FleetPins], list[Difference]]]`, and the
  "no fingerprint reported" error applies when any implemented role is on the host.
- Each role's `tasks/fingerprint.yml` accumulates into `fleet_fingerprint`
  (`set_fact: fleet_fingerprint: "{{ fleet_fingerprint | default({}) | combine({...}) }}"`), seeded
  with `system`/`machine` by a tiny `always` fingerprint step in `converge.yml`. The callback already
  emits on every such task and `parse_events` keeps the last, which is the superset. If that proves
  fragile in the first task, fall back to per-role `fleet_fingerprint_<role>` facts merged in Python;
  the executor decides based on a failing test, not preference.
- `Difference` stays `field / expected / actual`; the message format is unchanged.

### 2.3 Check-mode and apply rules for every new role

1. No network in check mode: `get_url`, `git`, `pip`, `npm`, `apt` are never invoked under
   `ansible_check_mode` (slice-2 precedent).
2. Read-only probes use `check_mode: false` and never write: `stat` with checksum, `slurp`,
   running the installed binary's `--version`, `stage-release.py --describe` (offline by design),
   `dpkg-query`, `systemctl --user show/is-active`, `tailscale status`.
3. Pending work in check mode must reach the report as `would_change`/`changed`. The "command with
   `creates`" trick used for Node is not general. Pattern for this slice: a gated marker task
   `when: ansible_check_mode and <pending>` that runs `ansible.builtin.command: {argv: [/bin/true]}`
   with `changed_when: true, check_mode: false`, named for the pending step ("stage herdr-engineering
   <sha12>"). It cannot fire in apply, so apply `changed` counts stay honest. Task T1 adds a real
   `ansible-playbook --check` test proving the marker appears as pending and that apply does not
   emit it; document the pattern in `docs/adding-a-role.md`.
4. Idempotence: every role's second apply reports `changed=0`. Each role gets an in-process test
   asserting this by counting `changed` task events from `run_playbook(...).stdout` via `parse_events`.
5. No root anywhere except `baseline` package installation and lingering (2.6). All other roles
   write only under `$HOME` and use `become: false`.
6. Writes under `$HOME` are atomic (temporary file then rename, or Ansible `copy`/`file`), and
   previous versions are never deleted (rollback is `apply` with an older lock, per spec 0001).

### 2.4 Python for herdr-eng

**OPEN QUESTION 2**: where does Python >= 3.12 come from on hosts whose `python3` is older.
Recommended default: the role uses variable `herdr_engineering_python` (default `python3`) and
asserts `>= 3.12` with a message naming the remedy ("install python 3.12 or newer, or set
`herdr_engineering_python`"); `baseline` records the host's `python3` version in its fingerprint
informationally. No lock-pinned Python bootstrap in slice 3. Container scenarios set the variable to the
`uv`-provided 3.12 already present in the test home. Alternative (not recommended now): pin a
python-build-standalone archive in the lock like Node.

### 2.5 The staging helper

`HE:ansible/files/stage-release.py` is vendored as `herdr_fleet/stage_release.py` (a real package
module: typed, ruff-clean, unit-tested, counted in coverage) and copied by the roles to
`~/.local/share/herdr-fleet/helpers/stage_release.py` (0700) before use; the source path reaches Ansible
as extra-var `fleet_stage_release_src` computed with `importlib.resources`. Constraints:

- Standard library only and no syntax newer than the oldest `python3` Ansible supports on targets
  (target Python can be 3.9-3.11 even when herdr-eng needs 3.12).
- The on-disk layout is **unchanged** from the existing helper (`~/.local/share/herdr-engineering/
  {herdr,pi}/releases|receipts|installed.json`, `~/.venvs/herdr-engineering`,
  `~/.pi/agent/settings.json` registration) so the existing rollout coordinator, `--describe`
  attestations and in-flight sessions keep working during migration.
- Changes from the original: repository URL becomes a required `--repository` argument (no
  hard-coded names); `--describe` stays offline; the interpreter used for the venv comes from
  `--python`; `npm` is found through a `--path-prefix` argument (the managed toolchain bin) so
  `npm ci` uses the pinned Node; the `runner` seam stays a plain function argument (not a mock: tests
  pass the real `run`).
- Not changed: write-once release directories, dirty/wrong-commit refusal, lock files, the
  `settings.json` merge semantics. Do not "improve" these; they carry the coordinator's safety.

**OPEN QUESTION 3**: vendor the helper (recommended; the old repo's `ansible/` is deleted in
migration step 3, and an import dependency on herdr-engineering would make herdr-fleet unbootstrappable)
versus reimplementing staging with Ansible `git`/`pip` modules (loses receipts and the PEP 610
attestation the coordinator relies on).

### 2.6 Root, linger and the `baseline` privilege rule

- `baseline` is the only role with `become`. Privileged tasks are exactly: Debian package install and
  `loginctl enable-linger <user>`. Both are preceded by read-only unprivileged probes, so check mode
  and a fully converged host never need root or a password.
- Missing packages under check mode produce a pending change naming the packages
  (`install packages: git, xz-utils`). Under apply without usable sudo the role fails with
  "baseline needs root to install: <names>; run `sudo apt-get install <names>` once, then re-run apply".
- Linger is only managed when `herdr-engineering` is among the host's roles and the host has a
  systemd user manager; probe with `loginctl show-user <user>` (as `HE:ansible/tasks/fabric-systemd.yml`
  does, grepping `Linger=yes`).
- macOS: baseline never installs Homebrew (needs interactive sudo, per the comment in
  `HE:ansible/roles/herdr_macos/tasks/main.yml`); it checks `git`, `curl`, `python3` and uses
  `community.general.homebrew`-free logic: when `brew` exists run `brew install` for missing ones
  without root, otherwise report the difference. Not covered by automated tests (no runner),
  see risks.

## 3. Role: `herdr`

Installs (all under `$HOME`, no root):

- Cache: `~/.local/share/herdr-fleet/herdr/cache/<version>/<asset>` (verified download kept so a
  rollback to a cached version needs no network).
- Binary: a real file `~/.local/bin/herdr`, mode 0755, installed from the cache with Ansible `copy`
  (`remote_src: true`; atomic rename; idempotent by checksum). **OPEN QUESTION 4**: real file
  (recommended; this is what upstream `install.sh` writes, so `herdr update` and the user's PATH
  behave as upstream expects) versus a symlink to a per-version directory (cleaner rollback, but
  untested interaction with `herdr update`).
- Never stops or restarts a running herdr server and never runs `herdr update`. Replacing the binary
  on disk leaves a running server on the old build until it is restarted or handed off; that
  activation is slice 4 and the fingerprint reports it (below).

Pins consumed: `herdr.version`, `herdr.sha256[<platform>]` (existing), asset table from `herdr.py`.
A missing platform key fails with "fleet.lock has no herdr.sha256 for <platform>; add the digest from
the release's GitHub asset (see docs)" (same style as the node assert).

Tasks (`roles/herdr/tasks/main.yml`): assert vars; derive platform and asset; create dirs; stat
installed binary with `checksum_algorithm: sha256`; if checksum differs from pin and not check mode:
`get_url` (URL `{{ herdr_release_base_url }}/v{{ version }}/{{ asset }}`, `checksum: sha256:<pin>`,
dest in cache), then `copy` to `~/.local/bin/herdr`; in check mode the pending marker (2.3).
A wrong checksum fails before anything is installed (get_url removes the download) and the old
binary stays.

Fingerprint section `herdr`:
`{path, present, version, sha256, resolved_path, resolves_to_installed, server_version}`.
`version` from `herdr --version` (first line `herdr X.Y.Z`); `resolved_path` from a clean-environment
`bash -lc 'command -v herdr'` exactly as the toolchain probe does (reuse by extracting the probe into
an included task file `roles/_common` or duplicating with a comment; duplication is acceptable for
slice 3 if it keeps roles independent); `server_version` from `herdr status` (informational, `null`
when no server).

Drift messages (`field: expected X, found Y`):

- `herdr: expected 0.9.1, found missing` / `found 0.9.5`
- `herdr binary: expected sha256 2a02..., found 9f3c...` (binary replaced by hand or `herdr update`
  even if the version string matches)
- `path herdr: expected <home>/.local/bin/herdr, found /usr/local/bin/herdr`
- `platform: a herdr sha256 for linux-x64 in fleet.lock, found none`
- `herdr server: expected 0.9.1, found 0.9.0 (restart or handoff pending)` is reported in JSON only,
  not as drift, in slice 3 (see section 9; **OPEN QUESTION 5**: recommended default is informational
  only until slice 4's session-aware activation exists, because treating it as drift would make every
  apply on a busy host fail verification).

## 4. Role: `herdr-engineering`

Installs, as the host user, `become: false`:

| What | Where |
|---|---|
| staging helper | `~/.local/share/herdr-fleet/helpers/stage_release.py` |
| immutable release checkout | `~/.local/share/herdr-engineering/herdr/releases/<sha>` (helper layout) |
| venv with `herdr-eng` | `~/.venvs/herdr-engineering` (helper layout) |
| stable entry point | symlink `~/.local/bin/herdr-eng -> ~/.venvs/herdr-engineering/bin/herdr-eng` |
| fabric env | `~/.config/herdr-engineering/fabric.env`, 0600, line `HERDR_ENGINEERING_FABRIC_URL=<services.gateway.url>`; other lines preserved (`lineinfile`) |
| fabric agent (Linux) | `~/.config/systemd/user/herdr-eng-fabric-agent.service`, rendered from the release's `scripts/herdr-eng-fabric-agent.service` with `__HERDR_ENG__` replaced by the venv `herdr-eng` path, as `HE:ansible/tasks/build-cleanup.yml` does for its unit; then user `daemon-reload`, `enabled`, `started`; restarted when the unit text or the staged revision changed |
| fabric agent (macOS) | run the release's `scripts/fabric-agent-macos.sh --repo <release>`; unverified in CI |
| build gate | `~/.pi/agent/extensions/build-gate.ts`, 0644, copied (`remote_src`) from the release's `deploy/pi-build-gate/build-gate.ts` |
| state/config dirs | owned by `baseline` (section 7), not duplicated here |

Pins consumed: `repos.herdr-engineering` (full commit), `repo_urls.herdr-engineering`,
`services.gateway.url` (config), optional `services.control_plane.url` (**OPEN QUESTION 1**: the
existing script defaults `HERDR_ENGINEERING_CONTROL_PLANE_URL` to a hard-coded LAN name,
`HE:scripts/fabric-agent.sh`; recommended default: write that line only when the config key is
present, never invent a default).

Flow:

1. Preconditions (assert): Python version, `git` present, full 40/64-hex revision.
2. Install helper (copy; 0700).
3. Probe `stage_release.py --kind herdr --describe` (offline, `check_mode: false`). If the
   attested revision equals the pin, skip staging (this is what makes the second apply a no-op).
4. Otherwise, not check mode: run the helper with `--repository`, `--revision`, `--python`,
   `--home`; check mode: pending marker.
5. Symlink the shim (gated on the venv entry point existing, like `toolchain_can_run`).
6. `fabric.env`, unit, service state, build gate (the latter three gated on the release
   directory existing so check mode on a fresh host reports the earlier pending step only).
7. Service state tasks run only when a systemd user manager is reachable
   (`systemctl --user show-environment` rc 0, probe fact `herdr_systemd_user`); otherwise the
   fingerprint field is `unmanaged` and no difference is raised. This is what lets container
   scenarios (no systemd) run the role.

Fingerprint section `herdr_engineering`:
`{revision, source_root, herdr_eng_version, venv_entry_present, shim_resolves, fabric_env_url,
fabric_unit_matches_release, fabric_service ("active"|"inactive"|"unmanaged"), build_gate_matches_release}`
where `revision` is the helper `--describe` attestation (null when invalid, covering a dirty
checkout, missing receipt, or a pip reinstall that broke `direct_url.json`) and
`build_gate_matches_release` compares sha256 of the installed file with the release's file (the old
fingerprint used md5 with no reference value to compare against).

Drift messages:

- `herdr-engineering revision: expected <sha>, found missing` / `found <other sha>`
- `herdr-eng: expected the venv entry point, found missing`
- `path herdr-eng: expected <home>/.local/bin/herdr-eng, found not found`
- `fabric.env: expected HERDR_ENGINEERING_FABRIC_URL=<url>, found <line|missing>`
- `fabric agent unit: expected rendered from release <sha12>, found missing|differs`
- `fabric agent: expected active, found inactive`
- `build-gate: expected sha256 of release <sha12>, found missing|<sha256>`

Non-drift notes: the staged helper installs `pip install --upgrade <release>`, so transitive
dependencies (`PyYAML`, `asyncpg`, `boto3`) are resolved from PyPI at stage time and are not pinned
(risk R2).

## 5. Role: `pi-runtime`

Staging only. Installs:

- Release checkout `~/.local/share/herdr-engineering/pi/releases/<sha>`, `node_modules` from
  `npm ci --include=dev` (helper layout), receipts and `installed.json`.
- Registration of that path in `~/.pi/agent/settings.json` `packages` (merge; other settings and
  packages untouched; multiple pi-engineering registrations remain a hard error as in the helper).

Pins consumed: `repos.pi-engineering`, `repo_urls.pi-engineering`; Node/npm from the `toolchain`
prefix via `--path-prefix ~/.local/share/herdr-fleet/toolchain/bin`.

Activation rule (this is the spec's "activation stays with the rollout coordinator"):

- The role never sends Pi control requests, never calls `herdr-eng fleet rollout ...`, never reloads,
  restarts, kills or signals a Pi process, never touches a mission, and never deletes an older
  release directory. Its docs and task names say "stage".
- Its fingerprint reports only what is staged and registered (the helper's `--describe` for kind
  `pi`: revision, source root, registered in settings, `node_modules` present). It never fingerprints
  what a running session has loaded. A host that has the pinned release staged while sessions still run
  the previous one is therefore not drift; loaded-versus-staged is slice 4 (coordinator integration).
- Registering the release in `settings.json` already changes what newly started Pi processes load.
  That is the existing behaviour of `HE:ansible/playbooks/pi-runtime-stage.yml` and is kept.
- **OPEN QUESTION 6**: two writers of the Pi revision. `herdr-eng fleet rollout plan --pi-revision X`
  (`HE:docs/operations/fleet-runtime-rollout.md`) stages X through its own playbook, while `apply`
  stages the lock's revision; if they differ the settings registration flip-flops and verification
  fails. Recommended default for slice 3: document that rollout revisions must equal
  `repos.pi-engineering`, and open a follow-up issue in herdr-engineering so the coordinator reads the
  revision from `fleet.lock` and calls `herdr-fleet apply --roles pi-runtime`. Slice 3 changes nothing
  in herdr-engineering.

Fingerprint section `pi_runtime`: `{revision, source_root, registered, node_modules_present}`.
Drift messages:

- `pi-runtime revision: expected <sha>, found missing` / `found <other sha>`
- `pi-runtime registration: expected <settings.json> to list <release path>, found not registered`
- `pi-runtime dependencies: expected <release>/node_modules, found missing`

Check mode: `--describe` probe only; pending marker when the revision differs; `npm ci` / git fetch
never run.

## 6. Role: `baseline`

Installs/ensures:

- Required packages (Debian/Ubuntu), fixed list in role defaults, justified by the other roles:
  `git curl ca-certificates python3 python3-venv xz-utils` (xz for the Node archive,
  `HF:herdr_fleet/ansible/roles/toolchain/tasks/main.yml`; git/venv/python for herdr-engineering).
  Plus `baseline.extra_packages` from config (the old role's vim/mc/btop go in the example config,
  not the defaults; **OPEN QUESTION 7**, recommended default as stated).
- Directories under `$HOME` (no root): `~/.config/herdr-engineering` (0700),
  `~/.local/share/herdr-engineering` (0700), `~/.local/share/herdr-engineering/state` and
  `.../artifacts` (0750, from `HE:ansible/playbooks/site.yml`'s state/artifact directories and
  `HE:scripts/install.sh` `STATE_DIR`/`ARTIFACT_DIR`), `~/.local/bin` (create only, never chmod).
  Config file contents (`config.yaml`, the lab LLM endpoint line in `HE:ansible/playbooks/site.yml`)
  are explicitly out of scope; see section 9.
- Tailscale presence: read-only `tailscale status`; informational.
- Linger (section 2.6).

Fingerprint section `baseline`:
`{packages: {name: installed|missing}, dirs: {path: mode|missing}, tailscale: "up"|"down"|"absent",
python3_version, linger: "yes"|"no"|"unmanaged"}`.
Drift messages: `package git: expected installed, found missing`;
`dir ~/.config/herdr-engineering: expected mode 0700, found missing`;
`linger <user>: expected yes, found no`. Tailscale is never drift (**OPEN QUESTION 8**: recommended
default informational, matching the old "informational, non-fatal" check in
`HE:ansible/playbooks/site.yml`; add config `baseline.require_tailscale` later if wanted).

## 7. Tests, by layer

Rules: no mocks, stubs or fakes of dependencies we own. The established hermetic precedent is a real
local HTTP server serving small real archives or scripts (tests/test_toolchain_apply.py); we follow
it and cover real third-party artifacts in the container layer.

Unit (`pytest`, no network, no containers):
- `herdr.py`: platform-to-asset table for all four platforms, pin parsing, missing-platform error,
  `compare` for each drift message above.
- `herdr_engineering.py`, `pi_runtime.py`, `baseline.py`: fingerprint JSON round trip (including
  absent sections), `compare` per message, rejection of malformed fingerprints.
- `pins.py` / `inventory.build_extra_vars`: the new extra-vars, `repo_urls` defaulting.
- Schemas: lock rejects `latest`, branch names, short SHAs and `http://` for `repo_urls`;
  config rejects bad package names; cross-check tests for the three new rules; example files
  still validate (`tests/test_config_schema.py`, `tests/test_lock_schema.py`).
- `plan` ordering already covers `ROLE_ORDER`; add the new example roles to `tests/test_plan.py`.
- `stage_release.py` against real `git` repositories created in `tmp_path` and addressed by `file://`
  URL: staging creates the release, receipt and `installed.json`; re-stage is a no-op;
  a dirty checkout and a wrong-commit checkout are refused; two concurrent stagers hit the lock;
  `--describe` returns nothing after the release is modified; settings merge preserves unrelated
  keys, refuses invalid JSON and multiple registrations; `kind=pi` uses a real `npm` script placed
  on `PATH` (as `FAKE_NPM` in the toolchain test); `kind=herdr` builds a tiny package with an in-tree
  PEP 517 backend (`backend-path = ["."]`, `requires = []`) so no PyPI access is needed.

Real-ansible in-process (`tests/test_<role>_apply.py`, throwaway `HOME`, `connection: local`,
`run_playbook` with `Mode.CHECK` / `APPLY` / `VERIFY`):
- Shared fixture module `tests/mirror.py` extracted from `test_toolchain_apply.py` (T0).
- `herdr`: mirror serves a small executable script named like the asset; apply installs it, verify
  is clean; second apply `changed=0`; wrong sha256 in the lock fails and leaves nothing and leaves an
  existing binary untouched; a binary replaced by hand is reported as `herdr binary: expected sha256`;
  check mode on a fresh HOME makes zero HTTP requests (the mirror counts requests) and lists a
  pending change; user `~/.local/bin` mode is preserved.
- `herdr-engineering` / `pi-runtime`: a local git repository fixture (real commits) addressed by
  `file://` extra-var; apply stages, shims, writes `fabric.env` 0600, copies the build gate, renders the
  unit file (service tasks skipped because no user systemd; fingerprint says `unmanaged`); second
  apply `changed=0`; changing the pinned commit stages a second release and keeps the first;
  a modified release checkout makes `--describe` fail and the fingerprint report missing revision;
  `pi-runtime` leaves unrelated `settings.json` content intact and the test asserts no Pi control
  process or rollout command is ever invoked (a `PATH`-first `herdr-eng` stub is NOT used; instead
  assert the task list of the role contains no `command` calling `rollout`, via parsing the role's YAML
  in a unit test, plus the behavioural assertion that sibling release directories survive).
- `baseline`: runs as the unprivileged test user, so tests cover check mode (missing package named in
  the report, using a package name guaranteed absent), probes, directory creation and modes, and the
  explicit no-root failure message; package installation itself is a container scenario.
- Cross-role: toolchain + herdr + herdr-engineering + pi-runtime on one local host in role order;
  verify the full fingerprint compares clean; second apply `changed=0`.

Container scenarios (`pytest -m container`, Docker and Apptainer, real downloads), added to
`tests/containers/`, fixture lock gets real pins:
- Fixture pins: herdr 0.9.1 with the four GitHub asset digests (list in T9); `herdr-engineering`
  and `pi-engineering` commits chosen from the public repositories (both returned HTTP 200
  unauthenticated); python override to the harness's `uv` 3.12.
- Scenarios: fresh host reports every new role's pending items (exit 2); apply then check exits 0;
  second apply `changed=0`; drift (herdr binary replaced by a different real release, herdr-eng
  venv removed, build-gate modified, `settings.json` registration removed, pinned commit changed)
  is named with the exact messages above and fixed by apply; a wrong herdr sha256 fails apply before
  installing; an older already-staged Pi release is untouched by apply of a newer pin
  (staging-only proof) and remains registered-or-not exactly as the helper specifies.
- `baseline` apply with real apt requires root: add a `user="root"` option to the harness's exec
  helper for this one scenario only (container-local root, still `--cap-drop ALL` where compatible;
  verify apt works under that profile in T8 and if not use `--cap-add` limited to what apt needs, never
  `--privileged`); all other scenarios stay non-root.
- Not coverable in containers: systemd user units (no user manager), linger, macOS paths. Their
  logic is covered by rendering/unit tests and the documented manual canary (section 8).
- Everything created is named `herdr-fleet-test-*`; no prune commands; HOME isolation rules in
  `HF:docs/testing.md` still hold (no real HOME bind mounts).

Smoke: `scripts/ci.sh` keeps running `validate` and `plan` over the examples; add
`herdr-fleet plan --check --connection local` is NOT added (it would touch the CI host); instead the
existing `plan` smoke over the extended examples is the smoke test.

## 8. Ordered task list for independent executors

Each task: TDD (failing tests first), `scripts/ci.sh` green, no mocks, small PR to
`feat/slice-3-installers` or its own branch. Tasks are ordered by dependency; T3, T4 and T7-T9 can
run in parallel after T1+T2 (and T4 before T5/T6/T7).

**T0 - Shared test mirror and pending-marker proof.**
Files: `tests/mirror.py` (new), `tests/test_toolchain_apply.py` (use it), `tests/test_check_marker.py`
(new), `docs/adding-a-role.md` (pattern note, section 2.3).
Tests first: real `ansible-playbook --check` on a throwaway role/playbook inside the test proves the
gated marker task is reported `changed` in check mode and not run in apply.
Acceptance: existing tests unchanged and passing; mirror exposes request counting and arbitrary files.

**T1 - Pins bundle, fingerprint sections, role registry.**
Files: `herdr_fleet/pins.py`, `herdr_fleet/fingerprint.py`, `herdr_fleet/report.py`,
`herdr_fleet/cli.py`, `herdr_fleet/inventory.py` (`build_extra_vars(lock, config)`),
`herdr_fleet/ansible/playbooks/converge.yml` (accumulating fingerprint seed), tests for each.
Tests first: fingerprint JSON with and without optional sections parses; `compare` registry dispatch;
toolchain-only hosts produce byte-identical reports to today.
Acceptance: all existing tests and container assertions pass untouched; adding a section requires no
edit to `report.py` beyond registering a comparator.

**T2 - Schema, config and example additions.**
Files: `herdr_fleet/schemas/fleet-lock.schema.json` (`repo_urls`), `fleet-config.schema.json`
(`baseline`, `services.control_plane`), `herdr_fleet/config.py` (cross-checks), `examples/*.yaml`,
`tests/containers/fixtures/*.yaml` (real herdr pins, see T9), `tests/test_lock_schema.py`,
`tests/test_config_schema.py`, `tests/test_plan.py`, `docs/adding-a-role.md` (compatibility rule).
Tests first: one rejection per new key; each cross-check message names file, key and remedy.
Acceptance: `herdr-fleet validate` passes for examples; old locks without new keys still load.

**T3 - `herdr` role.**
Files: `herdr_fleet/herdr.py`, `herdr_fleet/ansible/roles/herdr/{defaults,tasks}/`, `converge.yml`
include + fingerprint include, `inventory.py` (`IMPLEMENTED_ROLES += "herdr"`),
`tests/test_herdr.py`, `tests/test_herdr_apply.py`.
Tests first: unit table and compare; in-process apply/idempotence/check-no-network/wrong-checksum.
Acceptance: second apply `changed=0`; check mode makes zero requests; messages match section 3.

**T4 - Vendored staging helper.**
Files: `herdr_fleet/stage_release.py`, `tests/test_stage_release.py`, package data entry if needed.
Tests first: the real-git unit list in section 7.
Acceptance: behaviour parity with `HE:ansible/files/stage-release.py` for layout and refusals;
stdlib-only; `--describe` offline; no hard-coded repository names.

**T5 - `herdr-engineering` role, part A (stage, venv, shim, revision fingerprint).**
Files: role `herdr-engineering/{defaults,tasks}` (stage + shim + fingerprint of revision/version/path),
`herdr_fleet/herdr_engineering.py` (pins, fingerprint, compare), converge/inventory wiring,
`tests/test_herdr_engineering*.py`.
Acceptance: staged from a local git fixture; second apply `changed=0`; drift on revision and shim
named; check mode no git/pip.

**T6 - `herdr-engineering` role, part B (fabric.env, agent unit, build gate).**
Files: same role (`fabric.yml`, `build_gate.yml`, fingerprint extensions), tests.
Acceptance: `fabric.env` 0600 with preserved extra lines; unit text equals release template with
placeholder replaced; service tasks skipped with fingerprint `unmanaged` when no user systemd;
build gate sha256 compared with release file; `services.control_plane` handling per OPEN QUESTION 1.

**T7 - `pi-runtime` role.**
Files: role `pi-runtime`, `herdr_fleet/pi_runtime.py`, wiring, tests.
Acceptance: staging only (assertions in section 7); toolchain Node used for `npm ci`; older
releases preserved; unrelated Pi settings preserved.

**T8 - `baseline` role.**
Files: role `baseline` (probe tasks, explicit `become` only on install and linger tasks),
`herdr_fleet/baseline.py`, wiring, tests, harness `user=` option if needed.
Acceptance: check mode and converged hosts need no sudo; failure message names packages and remedy.

**T9 - Container scenarios and real pins.**
Files: `tests/containers/fixtures/fleet.lock.yaml` and `fleet-config.yaml`, `tests/containers/pins.py`
(alternate herdr version and digests), `tests/containers/test_installer_scenarios.py`,
`tests/containers/runtime.py` (root option), `docs/testing.md`.
Pin sourcing: herdr digests from the GitHub release API `assets[].digest` for the chosen tag
(and cross-check one against `herdr.dev/latest.json` for the newest tag); document the command in
`docs/adding-a-role.md`. Known current values for v0.9.1: linux-x64 `2a02fed16beb651ef006e1d43f048f652ca4dc58ad053cd2d44450563d5c54b7`,
linux-arm64 `f4ccf4de745f2cb9a39a983e9ba3703dad50ec2a58dea83026ceab721bbd8d9e`,
darwin-arm64 `5fc7a7e7adfaca56fa80aa89dcb025693357268dab8285b9ce2d08a2313c89de`,
darwin-x64 `053be0639935fe54ab5efbdb46651054e4f6a753a5b43153c88bd6912bce1e94`
(the executor must re-verify before committing; the plan only records what the API returned on
2026-10-30). Acceptance: scenarios in section 7 pass on Docker; Apptainer cases pass or skip with a
reason; no prune commands used.

**T10 - Docs and spec status.**
Files: `docs/specs/0001-...md` (role table wording: baseline's explicit root, pi-runtime staging),
`docs/adding-a-role.md`, `README.md`, `docs/testing.md`.
Acceptance: docs describe the pending-marker pattern, privilege rule, `repo_urls`, and slice 3 as done.

## 9. Out of scope (explicitly)

- Activation of anything: Pi reload, mission recovery, herdr server restart or `herdr update
  --handoff`, session-aware ordering, notifications, rollback orchestration (slice 4).
- `secret-scan`, `controller`, `gateway`, `ci-server`, `ci-agent`, backup/restore, `init` (slices 5-8).
- The enroll-sync watcher unit and the build-cleanup companion (`HE:ansible/tasks/fabric-systemd.yml`,
  `build-cleanup.yml`), and the web-serve service (`HE:ansible/tasks/web-serve.yml`).
  **OPEN QUESTION 9**: recommended default is a slice 3.5 follow-up before slice 8 deletes the old
  path, because these are parity gaps for `fleet-sync.yml`.
- Installing autospec (pinned in the lock but no slice-3 role consumes it; the old `main` clones are
  dropped, not ported) and Pi itself (the Pi CLI belongs to `toolchain`). **OPEN QUESTION 10**:
  which role should install autospec; recommended default is none until a role in spec 0001 names it.
- Machine-local `config.yaml` content (including the lab LLM endpoint line,
  `HE:ansible/playbooks/site.yml`, and `HE:scripts/install.sh` step 4 defaults), Tailscale enrolment
  and auth keys, and the web service. Hard-coded hosts and URLs from those files are not copied into
  this repo.
- Modifying herdr-engineering, including making the rollout coordinator read `fleet.lock`.
- Hash-locked Python dependencies for herdr-eng, RHEL-family, Windows, and a pinned Python bootstrap.
- Automated macOS testing (needs a runner; manual smoke only).

## 10. Risks

- R1 Upstream drift: `herdr update` or a package manager replaces the binary. Mitigated by the
  sha256 fingerprint; remains a human-process issue (spec 0001 "one update path").
- R2 Unpinned transitive Python dependencies when staging herdr-eng (`pip install` from PyPI):
  the same lock can produce different venvs over time. Accepted for slice 3; follow-up is a
  hash-pinned requirements file in the release.
- R3 Python 3.12 availability on older Debian (OPEN QUESTION 2).
- R4 The vendored helper diverges from the original during the migration window: keep the on-disk
  layout identical, add a test that reads a layout produced by the original script's documented
  paths, and treat any change in the original as a port item until step 3 of the migration.
- R5 Settings flip-flop between `apply` and the rollout coordinator (OPEN QUESTION 6).
- R6 systemd user services, linger and macOS launchd cannot be exercised in the container
  harness; they ship with rendering tests and a manual canary on one real host before the lock is
  rolled to the fleet.
- R7 Privilege boundary: `baseline` is the only root path. Review it with `/code-review` and keep a
  test that scans all other roles for `become: true`.
- R8 The accumulating `fleet_fingerprint` fact could be lost on a rescued host; T1 has a fallback
  design (per-role facts merged in Python) if tests show it.
- R9 Container tests download a 26 MB herdr binary and clone public repositories; CI needs network,
  and the public repositories must stay readable.
- R10 Pending-marker pattern is new; if Ansible changes check-mode semantics the T0 test catches it.

## 11. OPEN QUESTIONS (recommended defaults)

1. Control-plane URL for the fabric agent (`services.control_plane.url`)? Default: optional config key,
   written to `fabric.env` only when set; `services.gateway.url` is the fabric control URL.
2. Source of Python >= 3.12 on older hosts? Default: require it, assert with a clear message,
   override via `herdr_engineering_python`; no Python bootstrap in the lock.
3. Vendor `stage-release.py` as `herdr_fleet/stage_release.py`? Default: yes.
4. herdr binary as a real file at `~/.local/bin/herdr` or a symlink? Default: real file.
5. Should a stale running herdr server count as drift? Default: informational only until slice 4.
6. Pi revision ownership between `apply` and the rollout coordinator? Default: revisions must equal
   the lock; follow-up issue in herdr-engineering, no change there now.
7. Baseline package set? Default: fixed minimal required list plus config `baseline.extra_packages`;
   editors/monitors move to the example config.
8. Is Tailscale absence ever drift? Default: no, informational.
9. Port enroll-sync, build-cleanup and web-serve now or later? Default: later (slice 3.5), before the
   old path is removed.
10. Which role installs autospec? Default: none yet.
11. Repo URLs in the lock (`repo_urls`) rather than config? Default: lock, optional, defaulting to the
    current GitHub URLs.
