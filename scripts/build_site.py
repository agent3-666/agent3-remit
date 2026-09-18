"""Render the page from the same code path the command line uses.

Generated, never hand-written, so what a visitor reads cannot drift from what the service decided.

    python scripts/build_site.py
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from run import report  # noqa: E402

from remit.directory import HUB_BASE, fetch_records  # noqa: E402
from remit.fulfil import fulfil  # noqa: E402
from remit.keeperhub import KeeperHub  # noqa: E402

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:#0a0a0a; color:#f2f2f2;
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif; }
.wrap { max-width: 1000px; margin:0 auto; padding:44px 20px 80px; }
h1 { font-size:32px; margin:0 0 6px; letter-spacing:-.4px; }
.sub { color:#9a9a9a; margin:0 0 30px; font-size:17px; }
h2 { font-size:19px; margin:34px 0 12px; }
.panel { border:1px solid #262626; border-radius:12px; background:#0e0e0e; padding:18px 20px; margin-bottom:14px; }
.panel.blocked { border-color:#4a2a22; }
.panel.payable { border-color:#d4e85a; }
.name { font-size:18px; font-weight:600; }
.asks { color:#d4e85a; }
table { width:100%; border-collapse:collapse; font-size:13.5px; margin-top:6px; }
th { text-align:left; color:#9a9a9a; font-weight:500; padding:6px 8px; border-bottom:1px solid #262626; }
td { padding:7px 8px; border-bottom:1px solid #1b1b1b; color:#cfcfcf; vertical-align:top; }
.blocker { color:#ff8b6b; }
.field { color:#e8e8e8; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }
.probe { font-size:13px; color:#9a9a9a; margin-top:6px; }
.probe b { color:#cfcfcf; font-weight:500; }
.note { color:#9a9a9a; font-size:13.5px; border-top:1px solid #1b1b1b; margin-top:34px; padding-top:16px; }
code { background:#1b1b1b; padding:1px 6px; border-radius:4px; font-size:12.5px; }
.state { display:inline-block; border:1px solid #333; border-radius:20px; padding:3px 12px; font-size:12.5px; color:#cfcfcf; }
a { color:#d4e85a; }
"""


def build(out: Path) -> Path:
    data = report(os.environ.get("HUB_DIRECTORY_URL", HUB_BASE), int(os.environ.get("KEEPERHUB_CHAIN_ID", "11155111")))
    keys = data["key_names"]
    client = KeeperHub()

    # The other half of the story: can a stranger call this listing at all, using only what the
    # directory publishes? Run it against the first priced record, live, at build time.
    call = _call_the_priced_listing(data)

    priced_panels = []
    free_names = []
    silent_names = []
    for plan in data["plans"]:
        if any(b["kind"] == "not-priced" for b in plan["blocked_by"]):
            free_names.append(plan["record"])
            continue
        # A record with no payment block at all is neither priced nor free. Folding it into either
        # would be the same mistake this service exists to point out.
        if any(b["field"] == "payment" for b in plan["blocked_by"]):
            silent_names.append(plan["record"])
            continue
        probes = "".join(
            f'<div class="probe"><b>{html.escape(p["url"])}</b><br>'
            f'{"answers" if p["reachable"] else "no answer"}: {html.escape(p["note"])}</div>'
            for p in plan["probes"]
        )
        blockers = "".join(
            f'<tr><td class="field">{html.escape(b["field"])}</td><td class="blocker">{html.escape(b["why"])}</td></tr>'
            for b in plan["blocked_by"]
        )
        table = f'<table><tr><th>field</th><th>why this stops the payment</th></tr>{blockers}</table>' if blockers else ""
        asks = (
            f'<span class="asks">asks {plan["amount"]} {html.escape(plan["currency"] or "(no currency published)")}</span>'
            if plan["amount"] is not None
            else '<span class="asks">priced, amount not published</span>'
        )
        state = "payable" if plan["payable"] else "blocked"
        priced_panels.append(
            f'<div class="panel {state}"><div class="name">{html.escape(plan["record"])} &nbsp; {asks}</div>'
            f"{probes}{table}</div>"
        )

    settle_state = (
        '<span class="state">A KeeperHub key is configured, so settlement is live</span>'
        if client.can_settle
        else '<span class="state">No KeeperHub key is configured, so this service cannot settle. '
        "It says so rather than reporting a payment that did not happen.</span>"
    )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent3 Remit</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Agent3 Remit</h1>
<p class="sub">Turn a published quote into money that can actually move. When the published record is
not enough to pay anyone, name the field that is missing, and never guess it.</p>

<h2>What it read</h2>
<div class="panel">
It read <a href="{data['directory']}/api/resources">{html.escape(data['directory'])}</a>, the live
Agent3 Hub directory, over its public API. <b>{data['records']} records.</b> Read-only: this service
sits alongside the Hub and never writes to it. Read at {generated}.
<p class="probe"><b>The snapshot every quote on this page is pinned to.</b>
<code>sha256</code> of the exact bytes that endpoint served:
<code>{html.escape((data['snapshot'] or {}).get('digest', '-'))}</code>,
over {(data['snapshot'] or {}).get('bytes', 0)} bytes, read at
{html.escape((data['snapshot'] or {}).get('read_at', '-'))}. Anybody can recompute it:
<code>curl -s {html.escape(data['directory'])}/api/resources | shasum -a 256</code>. The digest
names <b>that one reading</b>. Getting a different one later is the mechanism working: the listing
has changed, so it is a different quote, and a payment agreed against the old one does not carry
over to it.</p>
</div>

<h2>Calling the listing</h2>
{call}

<h2>How this directory writes payment terms</h2>
<div class="panel">
<table>
<tr><th>written under</th><th>records</th><th>which</th></tr>
<tr><td class="field">payment.mode</td><td>{len(keys['payment.mode only'])}</td><td>{html.escape(', '.join(keys['payment.mode only']))}</td></tr>
<tr><td class="field">payment.model</td><td>{len(keys['payment.model only'])}</td><td>{html.escape(', '.join(keys['payment.model only']))}</td></tr>
<tr><td class="field">both</td><td>{len(keys['both keys'])}</td><td>{html.escape(', '.join(keys['both keys']) or '-')}</td></tr>
<tr><td class="field">neither</td><td>{len(keys['neither key'])}</td><td>{html.escape(', '.join(keys['neither key']))}</td></tr>
</table>
<p class="probe">A caller that reads only <code>payment.mode</code> sees nothing at all for
{len(keys['what a reader of payment.mode alone would miss'])} of these records. That is worse than it
sounds: an absent key and a free service are the same value, so those records look free rather than
unreadable. Both keys are read here, and neither is chosen as the winner.</p>
</div>

<h2>Priced records</h2>
{''.join(priced_panels)}
<div class="panel"><b>Says it is free, so nothing to settle:</b> {html.escape(', '.join(free_names))}</div>
<div class="panel"><b>Says nothing about payment at all:</b> {html.escape(', '.join(silent_names) or '-')}
<p class="probe">Silence is not the same as free. A caller that treats a missing payment block as
free will call this expecting to owe nothing, which is a guess about somebody else's terms.</p></div>

<h2>Settlement</h2>
{_receipt_panel(ROOT / "media" / "receipt.json")}
<div class="panel">{settle_state}<p class="probe">When a payment can be made, it goes through the
KeeperHub Direct Execution API: dry run first, then one broadcast under an idempotency key derived
from the job rather than the attempt, then the receipt is read back from the chain. Asking twice for
the same job replays the first result instead of paying twice.</p></div>

<div class="note">
<p><b>Settling the same quote twice.</b> The idempotency key is the quote digest, so the key is a
name for the job rather than for the attempt. On the live KeeperHub API, re-sending a job under the
same key returns the same execution id and the same transaction hash with no second transfer, while
a different job produces a second one.</p>
<p><b>What the probes do and do not show.</b> Each probe is one request from one machine. A host that
does not answer here may answer somewhere else, and this page does not claim otherwise. What makes
the reading worth something is that the probes run together: when one endpoint is silent while the
directory's own host and other endpoints answer in the same moment, the silence is about that
endpoint rather than about the network here.</p>
<p><b>Nothing is filled in.</b> Where a field is missing the page says which field. A guessed
recipient or a guessed chain produces a transaction that looks exactly like a correct one until the
money is gone.</p>
</div>
</div></body></html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def _receipt_panel(path: Path) -> str:
    """A settlement that happened, with the second attempt counted from outside.

    Every number here belongs to one recorded run and is labelled with when it was taken. Nothing on
    this page states how many transactions the wallet has sent in total, because that keeps moving:
    anyone settling again adds to it, and a figure written here would quietly go stale.
    """
    if not path.exists():
        return ""
    data = json.loads(path.read_text())
    rows = "".join(
        f'<tr><td>{html.escape(a["label"])}</td>'
        f'<td class="field">{html.escape(a["tx_hash"] or "-")}</td>'
        f'<td>{a["wallet_tx_count_before"]} &rarr; {a["wallet_tx_count_after"]}</td>'
        f'<td>{"nothing new reached the chain" if a["moved"] == 0 else "one transaction"}</td></tr>'
        for a in data["attempts"]
    )
    first = data["attempts"][0]
    link = data["explorer"] + (first["tx_hash"] or "")
    return (
        '<div class="panel payable"><b>A settlement, and the same job asked for twice.</b>'
        f'<table><tr><th>attempt</th><th>transaction</th><th>sent by this wallet</th><th></th></tr>{rows}</table>'
        f'<p class="probe">The count either side of each attempt is of transactions sent by '
        f'{html.escape(data["wallet_counted"])}, read from '
        f'<code>{html.escape(data["counted_from"])}</code> rather than from KeeperHub. '
        '"I did not send a second one" is the one statement that should not come from whoever would '
        'have sent it. These readings are from the run recorded at '
        f'{html.escape(data["recorded_at"])}; the wallet keeps sending after that, so the numbers '
        'belong to that run rather than describing the wallet today.</p>'
        f'<p class="probe">Transaction: <a href="{html.escape(link)}">{html.escape(link)}</a></p></div>'
    )


def _call_the_priced_listing(data: dict) -> str:
    """State what was observed, in order, and leave the conclusion to the reader."""
    priced = [
        p for p in data["plans"]
        if not any(b["kind"] == "not-priced" or b["field"] == "payment" for b in p["blocked_by"])
    ]
    if not priced:
        return ""
    target = priced[0]["record"]
    record = next((r for r in fetch_records(data["directory"]) if r.name == target), None)
    if record is None:
        return ""
    result = fulfil(record)

    steps = "".join(
        f'<tr><td>{html.escape(s.what)}</td><td class="field">{html.escape(s.url)}</td>'
        f'<td>{s.status}: {html.escape(s.detail)}</td></tr>'
        for s in result.steps
    )
    if result.delivered:
        verdict = (
            f'<p class="probe">The address that answered was <b>{html.escape(result.url_source or "")}</b>. '
            "So the service itself runs, and the half that does not work is the half that takes money.</p>"
        )
    else:
        verdict = (
            '<p class="probe">What the directory publishes was not enough to call this listing, and '
            "no other address was offered.</p>"
        )
    missing = "".join(f'<p class="probe blocker">MISSING {html.escape(m)}</p>' for m in result.missing)
    return (
        f'<div class="panel"><div class="name">{html.escape(record.name)}</div>'
        f'<table><tr><th>what was done</th><th>address</th><th>what came back</th></tr>{steps}</table>'
        f"{verdict}{missing}</div>"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "site" / "index.html"))
    args = parser.parse_args()
    print(f"wrote {build(Path(args.out))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
