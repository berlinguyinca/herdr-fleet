from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_lock
from tests.helpers import LOCK_EXAMPLE, Doc, example_lock, write_yaml

Mutator = Callable[[Doc], None]


def _set(path: list[str], value: object) -> Mutator:
    def apply(doc: Doc) -> None:
        node: Doc = doc
        for key in path[:-1]:
            node = node[key]  # type: ignore[assignment]
        node[path[-1]] = value

    return apply


def _drop(path: list[str]) -> Mutator:
    def apply(doc: Doc) -> None:
        node: Doc = doc
        for key in path[:-1]:
            node = node[key]  # type: ignore[assignment]
        del node[path[-1]]

    return apply


def test_example_lock_is_valid() -> None:
    lock = load_lock(LOCK_EXAMPLE)
    assert lock.version == 1


BAD_CASES: list[tuple[str, Mutator, str]] = [
    ("node range", _set(["node", "version"], "^22.0.0"), "node.version"),
    ("node latest", _set(["node", "version"], "latest"), "node.version"),
    ("node partial version", _set(["node", "version"], "22.11"), "node.version"),
    ("node bad sha", _set(["node", "sha256"], {"linux-x64": "abc"}), "node.sha256"),
    ("node bad platform", _set(["node", "sha256"], {"plan9": "0" * 64}), "node.sha256"),
    ("node no sha", _set(["node", "sha256"], {}), "node.sha256"),
    ("herdr range", _set(["herdr", "version"], ">=0.5.0"), "herdr.version"),
    ("herdr missing sha", _drop(["herdr", "sha256"]), "sha256"),
    ("repo branch", _set(["repos", "autospec"], "main"), "repos.autospec"),
    ("repo short sha", _set(["repos", "autospec"], "abc1234"), "repos.autospec"),
    ("repo uppercase sha", _set(["repos", "autospec"], "A" * 40), "repos.autospec"),
    ("repo 41 hex", _set(["repos", "autospec"], "a" * 41), "repos.autospec"),
    ("repo missing", _drop(["repos", "pi-engineering"]), "pi-engineering"),
    ("cli latest", _set(["clis", "codex"], "latest"), "clis.codex"),
    ("cli range", _set(["clis", "gitleaks"], "~8.21.0"), "clis.gitleaks"),
    ("cli missing", _drop(["clis", "woodpecker-cli"]), "woodpecker-cli"),
    (
        "image tag only",
        _set(["images", "woodpecker-server"], {"repository": "x/y", "digest": "v3"}),
        "images.woodpecker-server.digest",
    ),
    (
        "image short digest",
        _set(["images", "woodpecker-server"], {"repository": "x/y", "digest": "sha256:ab"}),
        "images.woodpecker-server.digest",
    ),
    (
        "image no digest",
        _set(["images", "woodpecker-server"], {"repository": "x/y:latest"}),
        "digest",
    ),
    ("fleet range", _set(["herdr_fleet", "version"], "*"), "herdr_fleet.version"),
    ("unknown top-level key", _set(["extra"], 1), "extra"),
    (
        "repo_urls http",
        _set(["repo_urls"], {"autospec": "http://git.example.com/a.git"}),
        "repo_urls",
    ),
    (
        "repo_urls ssh",
        _set(["repo_urls"], {"autospec": "ssh://git@git.example.com/a.git"}),
        "repo_urls",
    ),
    (
        "repo_urls credentials",
        _set(["repo_urls"], {"pi-engineering": "https://user:pw@git.example.com/a.git"}),
        "repo_urls.pi-engineering",
    ),
    (
        "repo_urls whitespace",
        _set(["repo_urls"], {"pi-engineering": "https://git.example.com/a b.git"}),
        "repo_urls.pi-engineering",
    ),
    (
        "repo_urls unknown repo",
        _set(["repo_urls"], {"mystery": "https://git.example.com/a.git"}),
        "mystery",
    ),
    (
        "repo_urls trailing newline",
        _set(["repo_urls"], {"autospec": "https://git.example.com/a.git\n"}),
        "repo_urls.autospec",
    ),
    (
        "repo_urls jinja",
        _set(["repo_urls"], {"autospec": "https://git.example.com/{{ lookup('pipe','id') }}"}),
        "repo_urls.autospec",
    ),
    (
        "repo_urls quote",
        _set(["repo_urls"], {"autospec": 'https://git.example.com/a"b'}),
        "repo_urls.autospec",
    ),
    (
        "repo_urls percent",
        _set(["repo_urls"], {"autospec": "https://git.example.com/%7B"}),
        "repo_urls.autospec",
    ),
    ("repo_urls branch spec", _set(["repo_urls"], {"autospec": "main"}), "repo_urls.autospec"),
]


@pytest.mark.parametrize(
    ("name", "mutate", "expected_path"), BAD_CASES, ids=[c[0] for c in BAD_CASES]
)
def test_lock_rejections(name: str, mutate: Mutator, expected_path: str, tmp_path: Path) -> None:
    doc = example_lock()
    mutate(doc)
    path = write_yaml(tmp_path / "fleet.lock", doc)
    with pytest.raises(ConfigError) as info:
        load_lock(path)
    text = str(info.value)
    assert str(path) in text
    assert expected_path in text


def test_example_lock_declares_repo_urls() -> None:
    urls = load_lock(LOCK_EXAMPLE).data["repo_urls"]
    assert set(urls) == {"herdr-engineering", "pi-engineering", "autospec"}  # type: ignore[call-overload]


def test_lock_without_repo_urls_still_loads(tmp_path: Path) -> None:
    doc = example_lock()
    del doc["repo_urls"]
    assert load_lock(write_yaml(tmp_path / "fleet.lock", doc)).version == 1


def test_repo_urls_may_name_a_subset(tmp_path: Path) -> None:
    doc = example_lock()
    doc["repo_urls"] = {"pi-engineering": "https://git.example.com/pi.git"}
    assert load_lock(write_yaml(tmp_path / "fleet.lock", doc)).version == 1
