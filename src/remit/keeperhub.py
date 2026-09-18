"""A KeeperHub Direct Execution client, written against the published API.

Written from https://docs.keeperhub.com/api/direct-execution rather than from any existing client, so
the shapes below come from the documentation. Where the documentation does not say something, the
code says so in an ASSUMPTION note rather than quietly deciding.

The order is fixed and is the whole point: simulate first, broadcast once, then read the receipt back
from the chain. Nothing is inferred at execution time.

No API key means no settlement. In that state this client reports that it cannot settle. It never
reports a payment that did not happen.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

BASE_URL = os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com")

# Documented: executions move through these, and the first three are not terminal.
NON_TERMINAL = {"pending", "running", "unconfirmed"}
TERMINAL_OK = "completed"
TERMINAL_FAIL = "failed"


@dataclass
class Attempt:
    """One call out to KeeperHub, kept so the demo can show exactly what was sent and returned."""

    step: str
    status_code: int | None
    ok: bool
    body: Any
    note: str = ""


@dataclass
class Settlement:
    settled: bool
    reason: str
    idempotency_key: str | None = None
    execution_id: str | None = None
    tx_hash: str | None = None
    chain_status: str | None = None
    explorer_url: str | None = None
    attempts: list[Attempt] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "settled": self.settled,
            "reason": self.reason,
            "idempotency_key": self.idempotency_key,
            "execution_id": self.execution_id,
            "tx_hash": self.tx_hash,
            "status": self.chain_status,
            "explorer_url": self.explorer_url,
            "attempts": [
                {"step": a.step, "http": a.status_code, "ok": a.ok, "note": a.note, "body": a.body} for a in self.attempts
            ],
        }


def idempotency_key(task_id: str, chain_id: int, recipient: str, amount: str, token: str | None) -> str:
    """Derived from the job, never from the attempt.

    Keyed on the attempt, a retry after a timeout would be a second payment. Keyed on the job, the
    retry is the same request and KeeperHub replays the original result.
    """
    material = f"{task_id}|{chain_id}|{recipient.lower()}|{amount}|{(token or 'native').lower()}"
    return hashlib.sha256(material.encode()).hexdigest()


class KeeperHub:
    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL, chain_id: int = 11155111):
        self.api_key = api_key if api_key is not None else os.environ.get("KEEPERHUB_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.chain_id = chain_id

    @property
    def can_settle(self) -> bool:
        return bool(self.api_key)

    def _call(self, method: str, path: str, body: dict | None = None, extra_headers: dict | None = None) -> Attempt:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "agent3-remit/1.0",
        }
        headers.update(extra_headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read().decode()
                parsed = json.loads(raw) if raw else {}
                hint = response.headers.get("X-Poll-Interval-Hint")
                return Attempt(path, response.status, True, parsed, f"poll hint {hint}" if hint else "")
        except urllib.error.HTTPError as err:
            raw = ""
            try:
                raw = err.read().decode()
            except Exception:
                pass
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw[:400]}
            note = ""
            if err.code == 409:
                # Documented: the same key with a different body is a conflict, not a retry.
                note = "idempotency_conflict: this key was used with different parameters"
            elif err.code == 429:
                note = "rate limited (the API allows 60 requests per minute per key)"
            return Attempt(path, err.code, False, parsed, note)
        except Exception as err:  # network-level
            return Attempt(path, None, False, {"error": str(err)}, "no response from KeeperHub")

    def broadcaster(self, recipient: str, amount: str, token_address: str | None = None) -> str | None:
        """Ask which account would broadcast, without broadcasting anything.

        The dry run neither signs nor sends, so this costs nothing. It matters because any check that
        counts transactions has to count the *sending* account: counting the payee reports no movement
        for every attempt, which reads exactly like a proven replay while proving nothing.

        ASSUMPTION(observed with a control, not documented): the sender is read from the first of
        these keys the dry run returns. What was observed, on the live API and not by this code: the
        same request sent twice with different recipientAddress values moved `to` (the organisation
        wallet, then a burn address) and left `from` unchanged. So `from` tracks the sender rather
        than the payee. It is not confirmed by the documentation, whose site renders client-side and
        curls down to an empty shell. If this returns nothing, this is where to look.
        """
        if not self.can_settle:
            return None
        body: dict[str, Any] = {"chainId": self.chain_id, "recipientAddress": recipient, "amount": amount}
        if token_address:
            body["tokenAddress"] = token_address
        dry = self._call("POST", "/api/execute/transfer", {**body, "simulate": True})
        if not dry.ok:
            return None
        found = _first(dry.body, ["from", "fromAddress", "sender", "walletAddress"])
        if not found and isinstance(dry.body, dict):
            for nested in ("transaction", "simulation", "data", "result"):
                inner = dry.body.get(nested)
                if isinstance(inner, dict):
                    found = _first(inner, ["from", "fromAddress", "sender", "walletAddress"])
                    if found:
                        break
        return str(found) if found else None

    def transfer(
        self,
        recipient: str,
        amount: str,
        task_id: str,
        token_address: str | None = None,
        poll_seconds: int = 90,
    ) -> Settlement:
        """Simulate, then broadcast once, then read the receipt back from the chain."""
        if not self.can_settle:
            return Settlement(
                settled=False,
                reason=(
                    "no KEEPERHUB_API_KEY is configured, so this service cannot settle anything. "
                    "Nothing was sent and no payment was made."
                ),
            )

        key = idempotency_key(task_id, self.chain_id, recipient, amount, token_address)
        body: dict[str, Any] = {"chainId": self.chain_id, "recipientAddress": recipient, "amount": amount}
        if token_address:
            body["tokenAddress"] = token_address

        settlement = Settlement(settled=False, reason="", idempotency_key=key)

        # 1. dry run. Documented as a boolean, and it creates no execution record.
        simulate_first = False
        simulate_first = True  # GUARD:simulate-before-broadcast
        dry = self._call("POST", "/api/execute/transfer", {**body, "simulate": True}) if simulate_first else None
        if dry is None:
            dry = Attempt("simulate", 200, True, {"skipped": True}, "")
        settlement.attempts.append(Attempt("simulate", dry.status_code, dry.ok, dry.body, dry.note))
        if not dry.ok:
            settlement.reason = f"the dry run was refused, so nothing was broadcast: {_short(dry.body)}"
            return settlement

        # 2. broadcast, once, under a key derived from the job.
        live = self._call("POST", "/api/execute/transfer", body, {"Idempotency-Key": key})
        settlement.attempts.append(Attempt("broadcast", live.status_code, live.ok, live.body, live.note))
        if not live.ok:
            settlement.reason = f"the broadcast was refused: {_short(live.body)}"
            return settlement

        # ASSUMPTION(docs do not name the response field): the execution identifier is read from the
        # first of these keys that is present. If KeeperHub names it differently, this is where to look.
        execution_id = _first(live.body, ["executionId", "id", "execution_id"])
        settlement.execution_id = execution_id
        if not execution_id:
            settlement.reason = "KeeperHub accepted the request but returned no execution id to follow"
            return settlement

        if live.body.get("idempotentReplay"):
            settlement.attempts[-1].note = "idempotentReplay: this job had already been executed, so it was not sent twice"

        # 3. poll until the receipt has been read back from the chain.
        deadline = time.time() + poll_seconds
        wait = 3
        while time.time() < deadline:
            status_call = self._call("GET", f"/api/execute/{execution_id}/status")
            settlement.attempts.append(Attempt("status", status_call.status_code, status_call.ok, status_call.body, status_call.note))
            state = str(_first(status_call.body, ["status", "state"]) or "").lower()
            settlement.chain_status = state or None
            tx_hash = _first(status_call.body, ["transactionHash", "txHash", "hash"])
            if tx_hash:
                settlement.tx_hash = tx_hash
            if state == TERMINAL_OK:
                settlement.settled = True
                settlement.reason = "KeeperHub executed the transfer and the receipt was read back from the chain"
                return settlement
            if state == TERMINAL_FAIL:
                settlement.reason = f"KeeperHub reported the execution failed: {_short(status_call.body)}"
                return settlement
            if state and state not in NON_TERMINAL:
                settlement.reason = f"unexpected status from KeeperHub: {state!r}"
                return settlement
            time.sleep(wait)
            wait = min(wait * 2, 15)

        settlement.reason = (
            f"the execution was accepted and is still {settlement.chain_status or 'in progress'} after {poll_seconds}s; "
            "it has an execution id and can be polled again, and the idempotency key means asking again will not pay twice"
        )
        return settlement


def _first(body: Any, names: list[str]) -> Any:
    if not isinstance(body, dict):
        return None
    for name in names:
        if body.get(name):
            return body[name]
    for nested in ("data", "execution", "result"):
        inner = body.get(nested)
        if isinstance(inner, dict):
            found = _first(inner, names)
            if found:
                return found
    return None


def _short(body: Any) -> str:
    text = json.dumps(body) if not isinstance(body, str) else body
    return text[:220]
