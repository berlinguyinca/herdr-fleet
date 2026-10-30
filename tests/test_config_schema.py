from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_config
from tests.helpers import CONFIG_EXAMPLE, Doc, example_config, write_yaml


def _hosts(doc: Doc) -> dict[str, Doc]:
    return doc["hosts"]  # type: ignore[return-value]


def _services(doc: Doc) -> Doc:
    return doc["services"]  # type: ignore[return-value]


def _expect_error(doc: Doc, tmp_path: Path, *fragments: str) -> None:
    path = write_yaml(tmp_path / "fleet-config.yaml", doc)
    with pytest.raises(ConfigError) as info:
        load_config(path)
    text = str(info.value)
    assert str(path) in text
    for fragment in fragments:
        assert fragment in text


def test_example_config_is_valid() -> None:
    config = load_config(CONFIG_EXAMPLE)
    assert [h.name for h in config.hosts] == ["host-a", "host-b"]
    assert config.hosts[1].jump_host == "host-a"
    assert config.secrets_path == "~/.config/herdr-fleet/vault"


def test_unknown_role_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _hosts(doc)["host-a"]["roles"] = ["baseline", "warp-drive"]  # type: ignore[index]
    _expect_error(doc, tmp_path, "hosts.host-a.roles", "warp-drive")


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    doc = example_config()
    doc["password"] = "hunter2"
    _expect_error(doc, tmp_path, "password")


def test_unknown_os_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _hosts(doc)["host-a"]["os"] = "plan9"  # type: ignore[index]
    _expect_error(doc, tmp_path, "hosts.host-a.os")


def test_missing_connection_user_rejected(tmp_path: Path) -> None:
    doc = example_config()
    del _hosts(doc)["host-a"]["connection"]["user"]  # type: ignore[index,call-overload]
    _expect_error(doc, tmp_path, "hosts.host-a.connection", "user")


def test_empty_roles_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _hosts(doc)["host-a"]["roles"] = []  # type: ignore[index]
    _expect_error(doc, tmp_path, "hosts.host-a.roles")


def test_duplicate_roles_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _hosts(doc)["host-a"]["roles"] = ["baseline", "baseline"]  # type: ignore[index]
    _expect_error(doc, tmp_path, "hosts.host-a.roles")


def test_secret_value_key_rejected(tmp_path: Path) -> None:
    doc = example_config()
    doc["secrets"] = {"path": "/x", "token": "abc"}
    _expect_error(doc, tmp_path, "secrets", "token")


def test_bad_service_url_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _services(doc)["gateway"] = {"url": "not a url"}  # type: ignore[index]
    _expect_error(doc, tmp_path, "services.gateway.url")


def test_bad_ingress_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _services(doc)["ci_server"]["ingress"] = "carrier-pigeon"  # type: ignore[index]
    _expect_error(doc, tmp_path, "services.ci_server.ingress")


def test_bad_repo_name_rejected(tmp_path: Path) -> None:
    doc = example_config()
    doc["woodpecker"] = {"repos": ["no-slash"]}
    _expect_error(doc, tmp_path, "woodpecker.repos")


def test_ci_agent_without_server_rejected(tmp_path: Path) -> None:
    doc = example_config()
    _hosts(doc)["host-a"]["roles"] = ["baseline"]  # type: ignore[index]
    del _services(doc)["ci_server"]["external_url"]  # type: ignore[call-overload]
    _expect_error(doc, tmp_path, "ci-agent", "ci_server")


def test_ci_agent_with_external_url_only_is_valid(tmp_path: Path) -> None:
    doc = example_config()
    _hosts(doc)["host-a"]["roles"] = ["baseline"]  # type: ignore[index]
    path = write_yaml(tmp_path / "c.yaml", doc)
    assert len(load_config(path).hosts) == 2


def test_ci_agent_with_local_server_only_is_valid(tmp_path: Path) -> None:
    doc = example_config()
    del _services(doc)["ci_server"]["external_url"]  # type: ignore[call-overload]
    path = write_yaml(tmp_path / "c.yaml", doc)
    assert len(load_config(path).hosts) == 2


def test_ci_server_without_ingress_rejected(tmp_path: Path) -> None:
    doc = example_config()
    del _services(doc)["ci_server"]["ingress"]  # type: ignore[call-overload]
    _expect_error(doc, tmp_path, "ci-server", "ingress", "tunnel", "public")


def test_ci_server_without_services_section_rejected(tmp_path: Path) -> None:
    doc = example_config()
    del doc["services"]
    _expect_error(doc, tmp_path, "ci-server", "ingress")
