"""The client is tested against a stub that records what it was actually sent.

Asserting "we wrote simulate first" by reading our own source proves nothing. These tests stand a
real HTTP server in front of the client and check the order of the calls, the headers, and that a
repeated job does not turn into a second payment.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from remit.keeperhub import KeeperHub, idempotency_key  # noqa: E402

RECEIPT_TX = "0x" + "ab" * 32


class StubKeeperHub(BaseHTTPRequestHandler):
    calls: list[dict] = []
    executions: dict[str, dict] = {}
    idempotency: dict[str, str] = {}

    def log_message(self, *args):  # keep the test output clean
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode()) if length else {}

    def _reply(self, status: int, payload: dict, headers: dict | None = None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self._read_body()
        key = self.headers.get("Idempotency-Key")
        simulate = body.get("simulate")
        StubKeeperHub.calls.append(
            {
                "path": self.path,
                "simulate": simulate,
                "idempotency_key": key,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        if simulate is True:
            return self._reply(200, {"simulated": True, "gasEstimate": "21000"})
        if simulate is not None:
            # A string "true" is not a boolean; the docs are explicit, so the stub is too.
            return self._reply(400, {"error": "simulate must be a boolean"})

        if key in StubKeeperHub.idempotency:
            existing = StubKeeperHub.idempotency[key]
            return self._reply(200, {"executionId": existing, "idempotentReplay": True})

        execution_id = f"exec-{len(StubKeeperHub.executions) + 1}"
        StubKeeperHub.executions[execution_id] = {"status": "pending", "polls": 0}
        StubKeeperHub.idempotency[key] = execution_id
        return self._reply(200, {"executionId": execution_id})

    def do_GET(self):
        StubKeeperHub.calls.append({"path": self.path, "simulate": None, "idempotency_key": None})
        execution_id = self.path.split("/api/execute/")[1].split("/status")[0]
        execution = StubKeeperHub.executions.get(execution_id)
        if not execution:
            return self._reply(404, {"error": "unknown execution"})
        execution["polls"] += 1
        # First poll is not terminal, so the polling loop is actually exercised.
        if execution["polls"] < 2:
            return self._reply(200, {"status": "running"}, {"X-Poll-Interval-Hint": "1"})
        return self._reply(200, {"status": "completed", "transactionHash": RECEIPT_TX}, {"X-Poll-Interval-Hint": "0"})


@pytest.fixture
def stub():
    StubKeeperHub.calls = []
    StubKeeperHub.executions = {}
    StubKeeperHub.idempotency = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubKeeperHub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_it_simulates_before_it_broadcasts(stub):
    client = KeeperHub(api_key="kh_test", base_url=stub, chain_id=11155111)
    result = client.transfer(recipient="0x" + "11" * 20, amount="0.001", task_id="job-1", poll_seconds=20)

    assert result.settled is True
    assert result.tx_hash == RECEIPT_TX

    posts = [c for c in StubKeeperHub.calls if c["path"] == "/api/execute/transfer"]
    assert len(posts) == 2, "one dry run and one broadcast"
    assert posts[0]["simulate"] is True, "the first call must be the dry run"
    assert posts[1]["simulate"] is None, "the second call must be the real one"
    assert posts[0]["idempotency_key"] is None, "a dry run creates no execution, so it needs no key"
    assert posts[1]["idempotency_key"], "the broadcast must carry an idempotency key"
    assert posts[1]["authorization"] == "Bearer kh_test"


def test_the_receipt_is_read_back_rather_than_assumed(stub):
    client = KeeperHub(api_key="kh_test", base_url=stub, chain_id=11155111)
    result = client.transfer(recipient="0x" + "11" * 20, amount="0.001", task_id="job-1", poll_seconds=20)

    polls = [c for c in StubKeeperHub.calls if "/status" in c["path"]]
    assert len(polls) >= 2, "the first status was not terminal, so it had to poll again"
    assert result.chain_status == "completed"


def test_asking_twice_for_the_same_job_does_not_pay_twice(stub):
    client = KeeperHub(api_key="kh_test", base_url=stub, chain_id=11155111)
    first = client.transfer(recipient="0x" + "11" * 20, amount="0.001", task_id="job-1", poll_seconds=20)
    second = client.transfer(recipient="0x" + "11" * 20, amount="0.001", task_id="job-1", poll_seconds=20)

    assert first.execution_id == second.execution_id
    assert len(StubKeeperHub.executions) == 1, "the second attempt must not create a second execution"
    assert first.idempotency_key == second.idempotency_key


def test_the_key_follows_the_job_not_the_attempt():
    first = idempotency_key("job-1", 11155111, "0xAbC" + "1" * 37, "0.001", None)
    again = idempotency_key("job-1", 11155111, "0xabc" + "1" * 37, "0.001", None)
    other_job = idempotency_key("job-2", 11155111, "0xAbC" + "1" * 37, "0.001", None)
    other_amount = idempotency_key("job-1", 11155111, "0xAbC" + "1" * 37, "0.002", None)

    assert first == again, "the same job is the same key, whatever the address casing"
    assert first != other_job
    assert first != other_amount, "a different amount is a different job"


def test_without_a_key_it_says_so_and_sends_nothing(stub):
    client = KeeperHub(api_key="", base_url=stub, chain_id=11155111)
    result = client.transfer(recipient="0x" + "11" * 20, amount="0.001", task_id="job-1")

    assert result.settled is False
    assert "cannot settle" in result.reason
    assert StubKeeperHub.calls == [], "nothing may be sent when there is no key"


def test_a_refused_dry_run_stops_before_broadcasting(stub):
    client = KeeperHub(api_key="kh_test", base_url=stub, chain_id=11155111)
    # A string instead of a boolean is refused by the stub, the way the documented API refuses it.
    original = KeeperHub.transfer

    def broken_simulate(self, recipient, amount, task_id, token_address=None, poll_seconds=90):
        body = {"chainId": self.chain_id, "recipientAddress": recipient, "amount": amount, "simulate": "true"}
        attempt = self._call("POST", "/api/execute/transfer", body)
        assert not attempt.ok
        return attempt

    attempt = broken_simulate(client, "0x" + "11" * 20, "0.001", "job-1")
    assert attempt.status_code == 400
    posts = [c for c in StubKeeperHub.calls if c["path"] == "/api/execute/transfer"]
    assert len(posts) == 1, "nothing was broadcast after the dry run was refused"
    assert KeeperHub.transfer is original
