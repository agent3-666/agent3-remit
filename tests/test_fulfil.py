"""Calling a listing with only what the directory publishes.

A stub stands in for the service so the three cases are exercised without depending on anyone's
uptime: the published address works, the published address is wrong but the service corrects it, and
the published address is wrong with no correction offered.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from remit.directory import Record, read_payment  # noqa: E402
from remit.fulfil import fulfil  # noqa: E402


class StubService(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, payload: dict | str, content_type: str = "application/json"):
        body = (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/works":
            return self._send(200, {"data": {"organic": [{"title": "a result", "link": "https://example.test"}]}})
        if path == "/wrong":
            # The shape that matters: the service names the endpoints it does have.
            return self._send(404, {"error": "Unknown endpoint", "received": "wrong", "available": ["works"]})
        if path == "/silent":
            return self._send(404, "<html><body>not found</body></html>", "text/html")
        return self._send(404, {"error": "no"})


@pytest.fixture
def service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def record_for(url: str) -> Record:
    return Record(
        resource_id="id-1",
        name="a listing",
        provider=None,
        payment=read_payment({"mode": "free"}),
        operations=[],
        interfaces=[{"url": url, "type": "api"}],
    )


def test_a_published_address_that_works_is_credited_to_the_directory(service):
    result = fulfil(record_for(f"{service}/works"))
    assert result.delivered is True
    assert result.url_source == "published by the directory"
    assert "a result" in (result.result_preview or "")


def test_a_correction_from_the_service_is_credited_to_the_service(service):
    result = fulfil(record_for(f"{service}/wrong"))
    assert result.delivered is True
    # The distinction this test exists for: it worked, but not from what the directory published.
    assert result.url_source == "corrected by the service itself, not published by the directory"
    assert result.called_url.endswith("/works?q=keeperhub")
    assert len(result.steps) == 2


def test_no_correction_means_it_is_reported_as_not_delivered(service):
    result = fulfil(record_for(f"{service}/silent"))
    assert result.delivered is False
    assert any("404" in m for m in result.missing)
    assert result.url_source is None


def test_a_record_with_no_callable_address_is_reported_before_any_call(service):
    record = Record(
        resource_id="id-2",
        name="relative only",
        provider=None,
        payment=read_payment({"mode": "free"}),
        operations=[],
        interfaces=[{"url": "/agent-cards/thing.json", "type": "agent"}],
    )
    result = fulfil(record)
    assert result.delivered is False
    assert result.steps == [], "nothing should be called when there is no address to call"
    assert any("no published interface names a host" in m for m in result.missing)


def test_the_correction_is_not_followed_when_that_is_refused(service):
    result = fulfil(record_for(f"{service}/wrong"), follow_correction=False)
    assert result.delivered is False
    assert len(result.steps) == 1
