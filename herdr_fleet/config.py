"""Load and validate ``fleet.lock`` and ``fleet-config.yaml``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import cast

import yaml
from jsonschema import Draft202012Validator

SUPPORTED_VERSION = 1

JsonObject = dict[str, object]


class ConfigError(Exception):
    """One or more problems in an input file; every message names file and path."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Host:
    name: str
    os: str
    roles: tuple[str, ...]
    address: str
    user: str
    jump_host: str | None


@dataclass(frozen=True)
class FleetConfig:
    hosts: tuple[Host, ...]
    ci_external_url: str | None
    ci_ingress: str | None
    gateway_url: str | None
    woodpecker_repos: tuple[str, ...]
    secrets_path: str | None


@dataclass(frozen=True)
class FleetLock:
    version: int
    data: JsonObject


def _schema(name: str) -> JsonObject:
    text = resources.files("herdr_fleet").joinpath("schemas", name).read_text()
    return cast(JsonObject, json.loads(text))


def _read_document(path: Path) -> JsonObject:
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ConfigError([f"{path}: file not found"]) from None
    except OSError as exc:
        raise ConfigError([f"{path}: cannot read file: {exc.strerror}"]) from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError([f"{path}: invalid YAML: {exc}"]) from exc
    if not isinstance(loaded, dict):
        raise ConfigError([f"{path}: top level must be a mapping with a 'version' key"])
    return cast(JsonObject, loaded)


def _check_version(path: Path, doc: Mapping[str, object]) -> None:
    if "version" not in doc:
        raise ConfigError([f"{path}: version: missing; supported: {SUPPORTED_VERSION}"])
    version = doc["version"]
    if version != SUPPORTED_VERSION or isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError(
            [
                f"{path}: version: version {version!r} unsupported; supported: "
                f"{SUPPORTED_VERSION}. To migrate, set 'version: {SUPPORTED_VERSION}' only "
                "after updating the file to that schema (see docs/ and the release notes "
                "of this herdr-fleet version), or install the herdr-fleet release that "
                f"supports version {version!r}."
            ]
        )


def _validate(path: Path, doc: JsonObject, schema_name: str) -> None:
    validator = Draft202012Validator(_schema(schema_name))
    errors = sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise ConfigError(
            [
                f"{path}: {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                for e in errors
            ]
        )


def load_lock(path: Path) -> FleetLock:
    doc = _read_document(path)
    _check_version(path, doc)
    _validate(path, doc, "fleet-lock.schema.json")
    return FleetLock(version=SUPPORTED_VERSION, data=doc)


def _section(doc: Mapping[str, object], key: str) -> JsonObject:
    return cast(JsonObject, doc.get(key) or {})


def _build_hosts(doc: Mapping[str, object]) -> tuple[Host, ...]:
    hosts: list[Host] = []
    for name, raw in cast(Mapping[str, JsonObject], doc["hosts"]).items():
        conn = cast(Mapping[str, str], raw["connection"])
        hosts.append(
            Host(
                name=name,
                os=cast(str, raw["os"]),
                roles=tuple(cast(list[str], raw["roles"])),
                address=conn["address"],
                user=conn["user"],
                jump_host=conn.get("jump_host"),
            )
        )
    return tuple(hosts)


def _cross_check_errors(path: Path, config: FleetConfig) -> list[str]:
    errors: list[str] = []
    has_server = config.ci_external_url is not None or any(
        "ci-server" in h.roles for h in config.hosts
    )
    by_name = {h.name: h for h in config.hosts}
    for host in config.hosts:
        jump = by_name.get(host.jump_host) if host.jump_host else None
        if jump is not None and jump.jump_host is not None:
            errors.append(
                f"{path}: hosts.{host.name}.connection.jump_host: '{jump.name}' itself has a "
                "jump_host; chained jump hosts are not supported (use one hop)"
            )
        if "ci-agent" in host.roles and not has_server:
            errors.append(
                f"{path}: hosts.{host.name}.roles: role 'ci-agent' needs a CI server; give a "
                "host the 'ci-server' role or set services.ci_server.external_url"
            )
        if "ci-server" in host.roles and config.ci_external_url is not None:
            errors.append(
                f"{path}: services.ci_server.external_url: host '{host.name}' has role "
                "'ci-server' but an external_url is also set; use either a provisioned "
                "server or an existing one, not both"
            )
        if "ci-server" in host.roles and config.ci_ingress is None:
            errors.append(
                f"{path}: services.ci_server.ingress: host '{host.name}' has role 'ci-server', "
                "which needs an ingress setting (tunnel or public) so GitHub can reach the webhook"
            )
    return errors


def load_config(path: Path) -> FleetConfig:
    doc = _read_document(path)
    _check_version(path, doc)
    _validate(path, doc, "fleet-config.schema.json")
    ci = cast(JsonObject, _section(doc, "services").get("ci_server") or {})
    config = FleetConfig(
        hosts=_build_hosts(doc),
        ci_external_url=cast("str | None", ci.get("external_url")),
        ci_ingress=cast("str | None", ci.get("ingress")),
        gateway_url=cast("str | None", _section(_section(doc, "services"), "gateway").get("url")),
        woodpecker_repos=tuple(cast(list[str], _section(doc, "woodpecker").get("repos", []))),
        secrets_path=cast("str | None", _section(doc, "secrets").get("path")),
    )
    errors = _cross_check_errors(path, config)
    if errors:
        raise ConfigError(errors)
    return config
