# Follow-up: the Python dependencies of herdr-engineering are not pinned or hashed

Status: herdr-fleet side done; one item remains open (herdr-engineering must commit its own
lock). Found in review of slice 3 (finding 5).

## The gap

`herdr-fleet` pins what it installs by checksum where it can: the Node archive and the herdr
binary are verified against sha256 values in `fleet.lock`, the herdr-engineering and
pi-engineering sources by full commit id, and the pi-engineering npm dependencies by that
repository's `package-lock.json` (`npm ci`).

The herdr-engineering venv is different. `stage_release.py` runs
`pip install --upgrade <release dir>`. herdr-engineering declares `PyYAML>=6.0`, `asyncpg>=0.29`
and `boto3>=1.34` in `pyproject.toml` and ships no requirements, constraints or lock file
(checked read-only: no `requirements*.txt`, `uv.lock` or `poetry.lock` in the repository). pip
therefore resolves those three and all their transitive dependencies (botocore, urllib3, ...) from
PyPI at install time, unpinned and without hashes. Two hosts staged on different days can get
different dependency versions for the same pinned commit.

So "pinned" and "checksum-verified" hold for the herdr-engineering source, not for its Python
dependencies. `herdr-fleet verify` attests the distribution (PEP 610 `direct_url.json`) but not
its dependency set.

## Remedy

Done in herdr-fleet:

1. The lock has an optional `python_requirements.herdr-engineering` field: `file` (a hashed
   requirements file shipped next to `fleet.lock`, inside its directory) and its `sha256`.
2. `stage_release.py` (`--requirements`, `--requirements-sha256`) and the herdr-engineering role
   verify the file's sha256, run `pip install --require-hashes -r <file>` and then
   `pip install --no-deps <release dir>`. A marker in the venv records the requirements sha256.
3. The fingerprint reports the requirements sha256 and `plan --check` names a mismatch
   ("python requirements").
4. With the field absent, behaviour is unchanged and `plan`, `plan --check` and `apply` print the
   informational note "unpinned python dependencies" (JSON `notes`).
5. Tested with real pip against a local wheel directory and index (`tests/wheels.py`,
   `tests/test_python_requirements*.py`).

Still open:

- herdr-engineering has to commit its own hashed lock (`uv pip compile --generate-hashes`, or
  pip-tools) so that fleet.lock can point at a real file. Until a lock carries that field, treat
  a herdr-engineering host as pinned to its source only.
- Known limit: pip build isolation fetches build-backend requirements (for example
  `setuptools`) unhashed when it builds the release directory. `--no-deps` stops dependency
  resolution but not that fetch. Pre-building a wheel would close it.
