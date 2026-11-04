"""Repository clone URLs, read from ``fleet.lock``'s optional ``repo_urls``.

There is deliberately no built-in default: a URL change is a trust change, so a role that clones
a repository requires the reviewed lock to say where from.
"""

from __future__ import annotations

from typing import cast

from herdr_fleet.config import ConfigError, FleetLock

REPO_NAMES: tuple[str, ...] = ("herdr-engineering", "pi-engineering", "autospec")


def repo_url(lock: FleetLock, name: str) -> str:
    urls = cast("dict[str, str]", lock.data.get("repo_urls") or {})
    if name not in urls:
        raise ConfigError(
            [
                f"fleet.lock: repo_urls.{name}: missing; add the HTTPS clone URL of the {name} "
                f"repository (for example 'repo_urls: {{{name}: https://git.example.com/org/"
                f"{name}.git}}')"
            ]
        )
    return urls[name]
