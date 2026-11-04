"""Typed readers for one role's fingerprint section; errors name ``<section>.<key>``."""

from __future__ import annotations

from collections.abc import Mapping

from herdr_fleet.fingerprint import FingerprintError

Section = Mapping[str, object]


def text(section: str, raw: Section, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise FingerprintError(f"fingerprint: {section}.{key}: expected a string, got {value!r}")
    return value


def optional_text(section: str, raw: Section, key: str) -> str | None:
    return None if raw.get(key) is None else text(section, raw, key)


def flag(section: str, raw: Section, key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise FingerprintError(f"fingerprint: {section}.{key}: expected a boolean, got {value!r}")
    return value
