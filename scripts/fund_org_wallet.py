"""Top up the KeeperHub organisation wallet from this project's own testnet wallet.

Dry run by default. It prints what it would send, what one settlement costs at the current gas price,
and how long the balance would last, and sends nothing until `--send` is passed.

    python scripts/fund_org_wallet.py --to 0xOrgWallet
    python scripts/fund_org_wallet.py --to 0xOrgWallet --amount 0.01 --send

Two rules this script exists to keep:
  * the money comes from this project's own wallet and from nowhere else
  * only enough is sent to cover the judging window, because the receiving key lives in KeeperHub's
    isolated environment and we cannot take it back
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RPC = os.environ.get("SEPOLIA_RPC", "https://ethereum-sepolia-rpc.publicnode.com")
CHAIN_ID = 11155111
TRANSFER_GAS = 21000
JUDGING_DAYS = 12


def rpc(method: str, params: list) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    # This public node refuses the default Python user-agent with a 403.
    request = urllib.request.Request(
        RPC, data=body, headers={"Content-Type": "application/json", "User-Agent": "agent3-remit/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload["result"]


def wei_to_eth(value: int) -> float:
    return value / 1e18


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="the KeeperHub organisation wallet")
    parser.add_argument("--amount", help="ETH to send, in whole units; the default is the sized estimate below")
    parser.add_argument("--settlements-per-day", type=int, default=20, help="how often the demo is expected to settle")
    parser.add_argument(
        "--gas-headroom",
        type=float,
        default=4.0,
        help="multiple of the current gas price to size the top-up at. Sizing at today's price is how "
        "a demo runs dry halfway through judging, because gas moves and this wallet cannot be topped "
        "up by anyone but us",
    )
    parser.add_argument("--send", action="store_true", help="actually broadcast; without this nothing is sent")
    args = parser.parse_args()

    # Signing libraries reject a mixed-case address that is not correctly checksummed, and
    # KeeperHub reports the wallet in lowercase. Normalise here so an address copied straight
    # out of an API response is usable rather than a TypeError at signing time.
    try:
        from eth_utils import to_checksum_address

        args.to = to_checksum_address(args.to)
    except ImportError:
        pass

    funder = os.environ.get("SEED_FUNDER_ADDRESS", "0x8e5830D9Cc2c88A330698A6B334ACF9c07c2acD4")
    balance = int(str(rpc("eth_getBalance", [funder, "latest"])), 16)
    gas_price = int(str(rpc("eth_gasPrice", [])), 16)
    org_balance = int(str(rpc("eth_getBalance", [args.to, "latest"])), 16)

    one_settlement = gas_price * TRANSFER_GAS
    settlements = args.settlements_per_day * JUDGING_DAYS
    at_todays_price = one_settlement * settlements
    needed = int(at_todays_price * args.gas_headroom)
    amount = args.amount if args.amount is not None else f"{wei_to_eth(needed) * 1.05:.4f}"

    print(f"chain            {CHAIN_ID} (Ethereum Sepolia)")
    print(f"from             {funder}  holds {wei_to_eth(balance):.4f} ETH")
    print(f"to               {args.to}  holds {wei_to_eth(org_balance):.4f} ETH")
    print(f"gas price now    {gas_price / 1e9:.3f} gwei")
    print(f"one settlement   {wei_to_eth(one_settlement):.8f} ETH  ({TRANSFER_GAS} gas)")
    print(
        f"judging window   {args.settlements_per_day}/day for {JUDGING_DAYS} days = {settlements} settlements"
    )
    print(f"  at today's gas {wei_to_eth(at_todays_price):.6f} ETH")
    print(f"  sized at {args.gas_headroom:g}x  {wei_to_eth(needed):.6f} ETH  (gas moves, and this wallet is ours to refill)")
    print(f"proposed send    {amount} ETH")

    if float(amount) < wei_to_eth(needed):
        print("\nNote: the proposed amount is below the sized estimate for the judging window.")
    if float(amount) > wei_to_eth(balance) / 2:
        print("\nNote: this would send more than half of what the funding wallet holds.")
        print("The receiving key lives in KeeperHub's isolated environment, so this cannot be pulled back.")

    if not args.send:
        print("\nDry run. Nothing was sent. Pass --send to broadcast.")
        return 0

    key = _funder_key()
    if not key:
        print("\nSEED_FUNDER_KEY is not set in .env, so there is nothing to sign with. Nothing was sent.")
        return 2

    try:
        from eth_account import Account
    except ImportError:
        print("\nSigning needs eth-account, which is not installed in this interpreter.")
        print("Install it (pip install eth-account) and run again. Nothing was sent.")
        return 2

    account = Account.from_key(key)
    if account.address.lower() != funder.lower():
        print(f"\nThe key in .env belongs to {account.address}, not {funder}. Nothing was sent.")
        return 2

    value = int(float(amount) * 1e18)
    nonce = int(str(rpc("eth_getTransactionCount", [funder, "pending"])), 16)
    tx = {
        "to": args.to,
        "value": value,
        "gas": TRANSFER_GAS,
        "maxFeePerGas": gas_price * 2,
        "maxPriorityFeePerGas": int(1e9),
        "nonce": nonce,
        "chainId": CHAIN_ID,
    }
    signed = account.sign_transaction(tx)
    # lstrip("0x") strips every leading "0" and "x" character, not the prefix, so a signed
    # transaction whose hex begins with a zero loses it and the node rejects an odd-length
    # string. removeprefix takes the prefix and nothing else.
    raw = signed.raw_transaction.hex()
    raw = "0x" + raw.removeprefix("0x")
    assert len(raw) % 2 == 0, f"signed transaction hex has odd length: {len(raw)}"
    tx_hash = str(rpc("eth_sendRawTransaction", [raw]))
    print(f"\nsent {amount} ETH to {args.to}")
    print(f"transaction: {tx_hash}")
    print(f"explorer:    https://sepolia.etherscan.io/tx/{tx_hash}")
    return 0


def _funder_key() -> str:
    """Read the funding key from this project's gitignored .env, or from the environment."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("SEED_FUNDER_KEY="):
                return line.partition("=")[2].strip()
    return os.environ.get("SEED_FUNDER_KEY", "")


if __name__ == "__main__":
    raise SystemExit(main())
