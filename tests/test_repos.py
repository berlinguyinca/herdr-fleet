"""Repository URLs come from the reviewed lock; there are no built-in defaults to trust."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_lock
from herdr_fleet.repos import REPO_NAMES, repo_url
from tests.helpers import LOCK_EXAMPLE, example_lock, write_yaml


def test_url_is_read_from_the_lock() -> None:
    lock = load_lock(LOCK_EXAMPLE)
    assert repo_url(lock, "pi-engineering") == lock.data["repo_urls"]["pi-engineering"]  # type: ignore[index]


def test_missing_url_error_names_file_key_and_remedy(tmp_path: Path) -> None:
    doc = example_lock()
    del doc["repo_urls"]
    lock = load_lock(write_yaml(tmp_path / "fleet.lock", doc))
    with pytest.raises(ConfigError) as info:
        repo_url(lock, "herdr-engineering")
    text = str(info.value)
    assert "fleet.lock" in text and "repo_urls.herdr-engineering" in text and "https://" in text


def test_repo_names_match_the_pinned_commits() -> None:
    assert set(REPO_NAMES) == set(load_lock(LOCK_EXAMPLE).data["repos"])  # type: ignore[call-overload]
