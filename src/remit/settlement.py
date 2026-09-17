"""Turns a published quote into a payment that can actually be made, or names what is missing.

The rule this module exists to enforce: **never fill in a field the record does not publish.** A
guessed recipient, a guessed chain or a guessed token produces a transaction that looks exactly like
a correct one right up until the money lands somewhere else. When something is missing, the answer is
the name of the missing field, not a default.
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from .directory import Record

Blocker = Literal["missing-field", "unreachable", "not-priced", "uncallable"]


@dataclass
class Finding:
    blocker: Blocker
    field_name: str
    detail: str


@dataclass
class Probe:
    url: str
    reachable: bool
    status: int | None
    note: str


@dataclass
class Plan:
    """Either a payment that can be made, or the reasons it cannot."""

    record_name: str
    resource_id: str
    amount: float | None = None
    currency: str | None = None
    payee: str | None = None
    payee_source: str | None = None
    chain_id: int | None = None
    findings: list[Finding] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)

    @property
    def payable(self) -> bool:
        return not self.findings and self.amount is not None and self.payee is not None

    def as_dict(self) -> dict:
        return {
            "record": self.record_name,
            "resource_id": self.resource_id,
            "payable": self.payable,
            "amount": self.amount,
            "currency": self.currency,
            "payee": self.payee,
            "payee_source": self.payee_source,
            "chain_id": self.chain_id,
            "blocked_by": [{"field": f.field_name, "why": f.detail, "kind": f.blocker} for f in self.findings],
            "probes": [{"url": p.url, "reachable": p.reachable, "status": p.status, "note": p.note} for p in self.probes],
        }


def probe(url: str, timeout: int = 12) -> Probe:
    """Ask a published endpoint whether it answers at all.

    Honest limit, stated wherever this is reported: this is one vantage point. A host that does not
    answer here may answer elsewhere. What makes the reading worth something is that it is taken
    alongside other hosts from the same machine at the same moment, so a local network fault shows up
    as everything failing, not one thing.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "agent3-remit/1.0"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(400).decode(errors="replace")
            return Probe(url, True, response.status, _describe(response.status, body))
    except urllib.error.HTTPError as err:
        body = ""
        try:
            body = err.read(400).decode(errors="replace")
        except Exception:
            pass
        # An HTTP error still means something answered.
        return Probe(url, True, err.code, _describe(err.code, body))
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError) as err:
        reason = getattr(err, "reason", err)
        return Probe(url, False, None, f"no response ({reason})")


def _describe(status: int, body: str) -> str:
    text = body.strip().replace("\n", " ")[:160]
    if status == 402 and "DEPLOYMENT_DISABLED" in body:
        # Worth separating: a disabled deployment also answers 402, and a reader looking only at the
        # status code would take it for an x402 payment challenge and try to pay.
        return "402 from a disabled deployment (DEPLOYMENT_DISABLED), which is not an x402 challenge"
    if status == 402:
        return f"402 payment required: {text}"
    return f"{status}: {text}" if text else str(status)


def build_plan(record: Record, chain_id: int, payee_override: str | None = None, check_liveness: bool = True) -> Plan:
    plan = Plan(record_name=record.name, resource_id=record.resource_id, chain_id=chain_id)
    payment = record.payment

    if payment.raw is None:
        plan.findings.append(
            Finding("missing-field", "payment", "the record publishes no payment block at all, so there is nothing to price")
        )
        return plan

    if payment.disagreement:
        plan.findings.append(Finding("missing-field", "payment.mode/payment.model", payment.disagreement))

    is_free = False
    is_free = not payment.looks_priced  # GUARD:free-short-circuit
    if is_free:
        plan.findings.append(
            Finding("not-priced", "payment.mode/payment.model", "the record says it is free, so there is nothing to settle")
        )
        return plan

    plan.amount = float(payment.amount) if payment.amount is not None else None
    plan.currency = payment.currency or None

    amount_missing = False
    amount_missing = plan.amount is None  # GUARD:amount-required
    if amount_missing:
        plan.findings.append(Finding("missing-field", "payment.amount", "priced, but the amount is not published"))

    currency_missing = False
    currency_missing = plan.currency is None  # GUARD:currency-required
    if currency_missing:
        plan.findings.append(Finding("missing-field", "payment.currency", "priced, but no currency is published"))

    reason = record.uncallable_reason
    if reason:
        plan.findings.append(Finding("uncallable", "interfaces", reason))

    # Who to pay. The directory record does not carry a payee address; it points at a gateway and a
    # config endpoint, and those are the only published places the address could come from.
    if check_liveness:
        for url, field_name in ((payment.gateway, "payment.gateway"), (payment.config_url, "payment.configUrl")):
            if not url:
                continue
            result = probe(url)
            plan.probes.append(result)
            dead = False
            dead = not result.reachable  # GUARD:unreachable-endpoint
            if dead:
                plan.findings.append(
                    Finding("unreachable", field_name, f"{url} did not answer, so it cannot tell us who to pay")
                )
            elif "DEPLOYMENT_DISABLED" in result.note:
                plan.findings.append(
                    Finding("unreachable", field_name, f"{url} answers {result.note}, so it cannot tell us who to pay")
                )

    if payee_override:
        plan.payee = payee_override
        plan.payee_source = "supplied by the caller, not by the directory"
        # A caller-supplied address settles who to pay, so the two reachability findings above are no
        # longer what blocks the payment. They stay on the record as findings about the directory.
        plan.findings = [f for f in plan.findings if f.blocker != "unreachable"]
    else:
        needs_payee = False
        needs_payee = plan.payee is None  # GUARD:never-guess-a-payee
        if needs_payee:
            plan.findings.append(
                Finding(
                "missing-field",
                "payee address",
                "no address to pay: the record publishes none, and the endpoints that could supply one did not",
                )
            )

    return plan
