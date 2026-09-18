"""Settle a quote, then ask for the same job again, and count the transactions from outside.

The claim worth doubting in a payment integration is "asking twice did not pay twice". This script
does not take anyone's word for it, ours or KeeperHub's: it reads the organisation wallet's
transaction count from a public Ethereum node before and after each attempt. A replay that really
replayed moves that count by zero.

Three attempts, in order:

  1. settle the quote                      -> the count must move by one
  2. settle the same quote again           -> the count must not move, and the execution id and the
                                              transaction hash must be the ones from attempt 1
  3. settle a deliberately different job   -> the count must move by one again

Writes media/receipt.json, which the demo video and the page read.

    python scripts/prove_replay.py --record "Agent3 Google Search API (Paid)" --payee 0xOrgWallet
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from remit.directory import HUB_BASE, fetch_records, snapshot_of  # noqa: E402
from remit.keeperhub import KeeperHub  # noqa: E402
from remit.settlement import build_plan  # noqa: E402

RPC = os.environ.get("SEPOLIA_RPC", "https://ethereum-sepolia-rpc.publicnode.com")
EXPLORER = "https://sepolia.etherscan.io/tx/"


def tx_count(address: str) -> int:
    """Read the wallet's transaction count from a public node.

    Deliberately not from KeeperHub. "I did not send a second one" is the one statement that should
    not come from the party that would have sent it.

    The count is of transactions this address has *sent*, so the address to watch is the wallet
    KeeperHub broadcasts from, not the one being paid. Watching the payee would report no movement
    for every attempt, which reads exactly like a proven replay while proving nothing.
    """
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount", "params": [address, "latest"]}
    ).encode()
    request = urllib.request.Request(
        RPC, data=body, headers={"Content-Type": "application/json", "User-Agent": "agent3-remit/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return int(str(payload["result"]), 16)


def settle(client: KeeperHub, payee: str, amount: str, task_id: str, label: str, wallet: str) -> dict:
    before = tx_count(wallet)
    result = client.transfer(recipient=payee, amount=amount, task_id=task_id)
    # The count is read after the receipt is terminal, so a pending broadcast is not mistaken for a
    # transaction that never happened.
    time.sleep(3)
    after = tx_count(wallet)
    print(f"  {label:26} count {before} -> {after}   tx {result.tx_hash or '-'}")
    return {
        "label": label,
        "task_id": task_id,
        "settled": result.settled,
        "execution_id": result.execution_id,
        "tx_hash": result.tx_hash,
        "reason": result.reason,
        "wallet_tx_count_before": before,
        "wallet_tx_count_after": after,
        "moved": after - before,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True, help="which listing to settle")
    parser.add_argument("--payee", required=True, help="the address to pay")
    parser.add_argument(
        "--wallet",
        help="the KeeperHub organisation wallet, which is the address that broadcasts. Its sent-count "
        "is what gets counted. Defaults to the payee, which is correct only for a self-transfer",
    )
    parser.add_argument("--amount", default="0.00001")
    parser.add_argument("--directory", default=os.environ.get("HUB_DIRECTORY_URL", HUB_BASE))
    parser.add_argument("--chain-id", type=int, default=int(os.environ.get("KEEPERHUB_CHAIN_ID", "11155111")))
    args = parser.parse_args()

    client = KeeperHub(chain_id=args.chain_id)
    if not client.can_settle:
        print("No KEEPERHUB_API_KEY is set, so nothing can be settled. Nothing was sent.")
        return 2

    records = fetch_records(args.directory)
    record = next((r for r in records if r.resource_id == args.record or r.name == args.record), None)
    if record is None:
        print(f"no record in the directory with id or name {args.record!r}")
        return 2

    snapshot = snapshot_of(args.directory)
    plan = build_plan(
        record,
        args.chain_id,
        payee_override=args.payee,
        snapshot=snapshot.as_dict() if snapshot else None,
        check_liveness=False,
    )
    # Which account actually broadcasts is a question only KeeperHub can answer, and the dry run
    # answers it without sending anything.
    wallet = args.wallet
    source = "given on the command line"
    if not wallet:
        wallet = client.broadcaster(args.payee, args.amount)
        source = "read from the dry run, which is the account that would broadcast"
    if not wallet:
        wallet = args.payee
        source = "fell back to the payee, because the dry run did not name a sender"
    print(f"counting {wallet}\n  ({source})")
    if wallet.lower() == args.payee.lower():
        print("  the payee and the sending account are the same address, so this is a self-transfer")

    print(f"quote digest {plan.quote_digest}")
    print(f"counting transactions of {wallet} from {RPC}\n")

    attempts = [
        settle(client, args.payee, args.amount, plan.quote_digest, "the quote", wallet),
        settle(client, args.payee, args.amount, plan.quote_digest, "the same quote again", wallet),
        settle(client, args.payee, args.amount, plan.quote_digest + "-different", "a different job", wallet),
    ]

    first, replay, different = attempts

    # An address that has still never sent anything cannot be the account that broadcast the
    # settlement above, so every reading here is about the wrong wallet. Checked after the first
    # attempt rather than before it: a newly created organisation wallet legitimately starts at zero,
    # and refusing to run on that would reject the very first settlement this project ever makes.
    if first["wallet_tx_count_after"] == 0:
        print(
            f"\n{wallet} has still sent no transactions after a settlement, so it is not the account "
            "that broadcast it. Every count below would be about the wrong wallet. Pass the "
            "organisation wallet with --wallet."
        )
        return 2

    # A wallet that never moves would satisfy "the replay moved nothing" for the wrong reason, so the
    # first and third attempts are what make the second one mean anything.
    checks = {
        "the first settlement moved the chain": first["moved"] == 1,
        "the replay moved nothing": replay["moved"] == 0,
        "the replay returned the same execution": replay["execution_id"] == first["execution_id"],
        "the replay returned the same transaction": replay["tx_hash"] == first["tx_hash"],
        "a different job moved the chain again": different["moved"] == 1,
    }
    print("")
    for label, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")

    out = ROOT / "media" / "receipt.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "chain_id": args.chain_id,
                "record": record.name,
                "quote": plan.quote,
                "quote_digest": plan.quote_digest,
                "amount": args.amount,
                "payee": args.payee,
                "wallet_counted": wallet,
                "counted_from": RPC,
                "explorer": EXPLORER,
                "attempts": attempts,
                "checks": checks,
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")
    if first["tx_hash"]:
        print(f"transaction: {EXPLORER}{first['tx_hash']}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
