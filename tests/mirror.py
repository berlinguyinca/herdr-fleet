"""A real local HTTP server for hermetic role tests.

It serves whatever bytes a test registers (a release asset, an archive, a package) and counts
every request, so a test can prove that check mode made none. Nothing here is a mock: Ansible's
``get_url`` talks to it over a real socket.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer


@dataclass
class RunningMirror:
    url: str
    files: dict[str, bytes]
    requests: list[str] = field(default_factory=list)


def _handler(mirror: RunningMirror) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            mirror.requests.append(self.path)
            payload = mirror.files.get(self.path)
            if payload is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    return Handler


@contextmanager
def serve_files(files: Mapping[str, bytes]) -> Iterator[RunningMirror]:
    """Serve ``files`` (URL path with leading slash -> bytes) on 127.0.0.1 until the block ends."""
    mirror = RunningMirror(url="", files=dict(files))
    server = HTTPServer(("127.0.0.1", 0), _handler(mirror))
    mirror.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield mirror
    finally:
        server.shutdown()
        server.server_close()
