# Follow-up: the Python dependencies of herdr-engineering are not pinned or hashed

Status: open. Found in review of slice 3 (finding 5).

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

1. herdr-engineering commits a hashed lock (`uv pip compile --generate-hashes`, or pip-tools) for
   its runtime dependencies.
2. `stage_release.py` installs from it with `pip install --require-hashes -r <lock>` and then
   `pip install --no-deps <release dir>`.
3. The fingerprint reports the lock's sha256 and `plan --check` names a mismatch.

Until then, treat a herdr-engineering host as pinned to its source only.
