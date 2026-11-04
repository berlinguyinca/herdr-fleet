"""The shared local HTTP mirror: a real server that serves arbitrary files and counts requests."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from tests.mirror import serve_files


def test_serves_registered_files_and_counts_every_request() -> None:
    with serve_files({"/v1/tool": b"payload"}) as mirror:
        with urllib.request.urlopen(f"{mirror.url}/v1/tool") as response:
            assert response.read() == b"payload"
            assert response.headers["Content-Length"] == "7"
        assert mirror.requests == ["/v1/tool"]


def test_unknown_path_is_404_and_still_counted() -> None:
    with serve_files({}) as mirror:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{mirror.url}/missing")
        assert caught.value.code == 404
        assert mirror.requests == ["/missing"]


def test_files_can_be_added_after_the_server_started() -> None:
    with serve_files({}) as mirror:
        mirror.files["/late"] = b"x"
        with urllib.request.urlopen(f"{mirror.url}/late") as response:
            assert response.read() == b"x"


def test_a_stopped_mirror_refuses_connections() -> None:
    with serve_files({}) as mirror:
        url = mirror.url
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"{url}/anything", timeout=2)
