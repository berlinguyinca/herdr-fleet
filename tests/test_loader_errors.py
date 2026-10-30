from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_config, load_lock
from tests.helpers import example_config, example_lock, write_yaml


def test_missing_file_names_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="nope.yaml") as info:
        load_lock(missing)
    assert "not found" in str(info.value)


def test_invalid_yaml_names_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("a: [unclosed\n")
    with pytest.raises(ConfigError, match="bad.yaml") as info:
        load_config(path)
    assert "YAML" in str(info.value)


def test_non_mapping_document(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_lock(path)


def test_missing_version(tmp_path: Path) -> None:
    doc = example_lock()
    del doc["version"]
    with pytest.raises(ConfigError, match="version"):
        load_lock(write_yaml(tmp_path / "l.yaml", doc))


@pytest.mark.parametrize("bad", [2, 0, "1", "latest"])
def test_unsupported_version_message_explains_migration(bad: object, tmp_path: Path) -> None:
    doc = example_config()
    doc["version"] = bad
    with pytest.raises(ConfigError) as info:
        load_config(write_yaml(tmp_path / "c.yaml", doc))
    text = str(info.value)
    assert f"version {bad!r} unsupported" in text
    assert "supported: 1" in text
    assert "migrat" in text


def test_all_errors_are_reported(tmp_path: Path) -> None:
    doc = example_lock()
    doc["repos"]["autospec"] = "main"  # type: ignore[index]
    doc["clis"]["codex"] = "latest"  # type: ignore[index]
    with pytest.raises(ConfigError) as info:
        load_lock(write_yaml(tmp_path / "l.yaml", doc))
    assert len(info.value.errors) == 2
