"""One test per rule, aimed at the record that rule exists to catch.

Fixtures here are shaped like the live directory, and tests/test_live_directory.py checks that claim
against the real thing rather than trusting these shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from remit.directory import Record, key_name_summary, read_payment  # noqa: E402
from remit.settlement import build_plan  # noqa: E402

CHAIN = 11155111


def record(name: str, payment: dict | None, interfaces=None) -> Record:
    return Record(
        resource_id=f"id-{name}",
        name=name,
        provider=None,
        payment=read_payment(payment),
        operations=[],
        interfaces=interfaces if interfaces is not None else [{"url": "https://example.test", "type": "api"}],
    )


def fields_blocked(plan) -> set[str]:
    return {f.field_name for f in plan.findings}


def test_a_record_using_the_other_key_name_is_not_missed():
    records = [
        record("uses mode", {"mode": "free"}),
        record("uses model", {"model": "free", "amount": 0}),
        record("uses neither", None),
    ]
    summary = key_name_summary(records)

    assert summary["payment.mode only"] == ["uses mode"]
    assert summary["payment.model only"] == ["uses model"]
    # The point of reading both: a caller who knows one key is blind to these.
    assert summary["what a reader of payment.mode alone would miss"] == ["uses model", "uses neither"]


def test_free_records_are_not_dragged_into_settlement():
    plan = build_plan(record("free thing", {"mode": "free"}), CHAIN, check_liveness=False)
    assert [f.blocker for f in plan.findings] == ["not-priced"]
    assert plan.payable is False


def test_a_price_with_no_currency_names_the_missing_field():
    plan = build_plan(record("priced", {"mode": "usage-based", "amount": 0.01}), CHAIN, check_liveness=False)
    assert "payment.currency" in fields_blocked(plan)


def test_a_price_with_no_amount_names_the_missing_field():
    plan = build_plan(record("priced", {"mode": "usage-based", "currency": "USD"}), CHAIN, check_liveness=False)
    assert "payment.amount" in fields_blocked(plan)


def test_no_payee_anywhere_is_refused_rather_than_guessed():
    plan = build_plan(
        record("priced", {"mode": "usage-based", "amount": 0.01, "currency": "USD"}), CHAIN, check_liveness=False
    )
    assert "payee address" in fields_blocked(plan)
    assert plan.payable is False
    assert plan.payee is None


def test_a_caller_supplied_payee_is_recorded_as_such():
    plan = build_plan(
        record("priced", {"mode": "usage-based", "amount": 0.01, "currency": "USD"}),
        CHAIN,
        payee_override="0x" + "11" * 20,
        check_liveness=False,
    )
    assert plan.payable is True
    assert plan.payee_source == "supplied by the caller, not by the directory"


def test_an_interface_with_no_host_is_reported_as_uncallable():
    plan = build_plan(
        record(
            "relative only",
            {"mode": "usage-based", "amount": 0.01, "currency": "USD"},
            interfaces=[{"url": "/agent-cards/thing.json", "type": "agent"}],
        ),
        CHAIN,
        payee_override="0x" + "11" * 20,
        check_liveness=False,
    )
    assert "interfaces" in fields_blocked(plan)
    assert plan.payable is False


def test_a_payment_is_bound_to_the_listing_it_came_from():
    """A bare transfer proves nothing. This one names what it is for."""
    snapshot = {"digest": "d" * 64, "read_at": "2026-09-17T19:00:00+00:00"}
    plan = build_plan(
        record("priced", {"mode": "usage-based", "amount": 0.01, "currency": "USD"}),
        CHAIN,
        payee_override="0x" + "11" * 20,
        check_liveness=False,
        operation="Google Web Search",
        snapshot=snapshot,
    )

    assert plan.quote["resource_id"] == "id-priced"
    assert plan.quote["operation"] == "Google Web Search"
    assert plan.quote["published_amount"] == 0.01
    assert plan.quote["directory_snapshot"] == "d" * 64
    assert len(plan.quote_digest) == 64


def test_a_different_published_price_is_a_different_job():
    """Otherwise a re-quote could replay the old payment under the same idempotency key."""
    snapshot = {"digest": "d" * 64, "read_at": "2026-09-17T19:00:00+00:00"}

    def digest_for(amount: float) -> str:
        return build_plan(
            record("priced", {"mode": "usage-based", "amount": amount, "currency": "USD"}),
            CHAIN,
            payee_override="0x" + "11" * 20,
            check_liveness=False,
            operation="op",
            snapshot=snapshot,
        ).quote_digest

    assert digest_for(0.01) != digest_for(0.02)


def test_a_changed_directory_snapshot_is_a_different_job():
    """The price was published at a moment. A later listing is not the same quote."""

    def digest_for(digest: str) -> str:
        return build_plan(
            record("priced", {"mode": "usage-based", "amount": 0.01, "currency": "USD"}),
            CHAIN,
            payee_override="0x" + "11" * 20,
            check_liveness=False,
            operation="op",
            snapshot={"digest": digest, "read_at": "2026-09-17T19:00:00+00:00"},
        ).quote_digest

    assert digest_for("a" * 64) != digest_for("b" * 64)


def test_an_endpoint_that_does_not_answer_is_reported_as_such():
    # Port 1 on localhost refuses immediately, which is the "no answer" case without a network call.
    plan = build_plan(
        record("priced", {"mode": "usage-based", "amount": 0.01, "currency": "USD", "gateway": "http://127.0.0.1:1/pay"}),
        CHAIN,
        check_liveness=True,
    )
    assert "payment.gateway" in fields_blocked(plan)
    assert plan.probes and plan.probes[0].reachable is False


def test_a_disabled_deployment_is_not_mistaken_for_a_payment_challenge():
    from remit.settlement import _describe

    disabled = _describe(402, "Payment required\n\nDEPLOYMENT_DISABLED\n\nhnd1::abc")
    real_challenge = _describe(402, '{"accepts":[{"scheme":"exact","payTo":"0xabc"}]}')

    assert "not an x402 challenge" in disabled
    assert "not an x402 challenge" not in real_challenge
    assert "payment required" in real_challenge


def test_the_two_key_names_disagreeing_is_reported_not_resolved():
    plan = build_plan(
        record("disagrees", {"mode": "free", "model": "usage-based", "amount": 0.01, "currency": "USD"}),
        CHAIN,
        payee_override="0x" + "11" * 20,
        check_liveness=False,
    )
    assert "payment.mode/payment.model" in fields_blocked(plan)
    assert plan.payable is False
