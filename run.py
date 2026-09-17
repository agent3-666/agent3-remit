"""Agent3 Remit: turn a published quote into money that can actually move, or name what is missing.

    python run.py                      # read the live directory and price every record
    python run.py --json
    python run.py --pay <resource_id> --payee 0x...   # settle through KeeperHub

Reads the Agent3 Hub directory over its public API and never writes to it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from remit.directory import HUB_BASE, fetch_records, key_name_summary  # noqa: E402
from remit.keeperhub import KeeperHub  # noqa: E402
from remit.settlement import build_plan  # noqa: E402

CHAIN_ID = int(os.environ.get("KEEPERHUB_CHAIN_ID", "11155111"))


def report(base: str, chain_id: int, check_liveness: bool = True) -> dict:
    records = fetch_records(base)
    summary = key_name_summary(records)
    plans = [build_plan(r, chain_id, check_liveness=check_liveness) for r in records]
    return {
        "directory": base,
        "chain_id": chain_id,
        "records": len(records),
        "key_names": summary,
        "plans": [p.as_dict() for p in plans],
    }


def print_report(data: dict) -> None:
    print("Agent3 Remit")
    print("=" * 74)
    print(f"Directory: {data['directory']}   records: {data['records']}   chain: {data['chain_id']}")
    print("")

    keys = data["key_names"]
    print("How this directory writes payment terms")
    print(f"  under payment.mode only : {len(keys['payment.mode only'])}")
    print(f"  under payment.model only: {len(keys['payment.model only'])}")
    print(f"  under both keys         : {len(keys['both keys'])}")
    print(f"  under neither key       : {len(keys['neither key'])}")
    missed = keys["what a reader of payment.mode alone would miss"]
    print(f"  a caller reading only payment.mode is blind to {len(missed)} record(s): {', '.join(missed) or 'none'}")
    print("  (an absent key and a free service look identical, which is why both are read)")
    print("")

    payable = [p for p in data["plans"] if p["payable"]]
    priced = [p for p in data["plans"] if p["blocked_by"] or p["payable"]]
    print(f"Priced records that can be paid right now: {len(payable)} of {len([p for p in priced if not _is_free(p)])}")
    print("")

    for plan in data["plans"]:
        if _is_free(plan):
            continue
        print(f"{plan['record']}")
        if plan["amount"] is not None:
            print(f"  asks {plan['amount']} {plan['currency'] or '(no currency published)'}")
        for probe in plan["probes"]:
            mark = "answers" if probe["reachable"] else "no answer"
            print(f"  probe {probe['url']}")
            print(f"        {mark}: {probe['note']}")
        for blocker in plan["blocked_by"]:
            print(f"  BLOCKED {blocker['field']}: {blocker['why']}")
        if plan["payable"]:
            print(f"  PAYABLE to {plan['payee']} ({plan['payee_source']})")
        print("")


def _is_free(plan: dict) -> bool:
    return any(b["kind"] == "not-priced" for b in plan["blocked_by"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Price what a directory publishes, and settle what can be settled.")
    parser.add_argument("--directory", default=os.environ.get("HUB_DIRECTORY_URL", HUB_BASE))
    parser.add_argument("--chain-id", type=int, default=CHAIN_ID)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-probe", action="store_true", help="skip the liveness probes")
    parser.add_argument("--pay", metavar="RESOURCE_ID", help="settle this record through KeeperHub")
    parser.add_argument("--payee", help="the address to pay, when the directory does not publish one")
    parser.add_argument("--amount", help="override the amount, in whole units")
    parser.add_argument("--task-id", default="remit-demo", help="names the job; the idempotency key is derived from it")
    args = parser.parse_args()

    if args.pay:
        return settle(args)

    data = report(args.directory, args.chain_id, check_liveness=not args.no_probe)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_report(data)
    return 0


def settle(args) -> int:
    records = fetch_records(args.directory)
    record = next((r for r in records if r.resource_id == args.pay or r.name == args.pay), None)
    if record is None:
        print(f"no record in the directory with id or name {args.pay!r}")
        return 2

    plan = build_plan(record, args.chain_id, payee_override=args.payee)
    print(f"Settling: {record.name}")
    for probe in plan.probes:
        print(f"  probe {probe.url}: {'answers' if probe.reachable else 'no answer'} — {probe.note}")
    for finding in plan.findings:
        print(f"  BLOCKED {finding.field_name}: {finding.detail}")

    amount = args.amount or (str(plan.amount) if plan.amount is not None else None)
    if not plan.payable or not amount:
        print("\nNothing was sent. This is the answer, not a failure: the published record does not")
        print("contain enough to pay anyone, and guessing the missing part would move real money.")
        return 1

    client = KeeperHub(chain_id=args.chain_id)
    if not client.can_settle:
        print("\nNo KEEPERHUB_API_KEY is set, so this service cannot settle. Nothing was sent.")
        print("The plan above is what would be executed once a key is configured.")
        return 1

    print(f"\n  paying {amount} to {plan.payee} on chain {args.chain_id} through KeeperHub")
    result = client.transfer(recipient=plan.payee, amount=amount, task_id=args.task_id)
    for attempt in result.attempts:
        print(f"  {attempt.step:<10} http {attempt.status_code} {'ok' if attempt.ok else 'refused'} {attempt.note}")
    print("")
    print(result.reason)
    if result.tx_hash:
        print(f"transaction: {result.tx_hash}")
        print(f"explorer:    https://sepolia.etherscan.io/tx/{result.tx_hash}")
    return 0 if result.settled else 1


if __name__ == "__main__":
    raise SystemExit(main())
