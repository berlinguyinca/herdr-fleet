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
