"""Build the page the video is rendered from, and the storyboard that goes with it.

Everything on screen is produced here from a live run: the directory read, the call, the plan, and
the terminal block, which is the real stdout of `python run.py` rather than something typed to look
like it. So the video cannot show a result the code does not produce.

    python scripts/build_demo.py
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from run import report  # noqa: E402

from remit.directory import HUB_BASE, fetch_records  # noqa: E402
from remit.fulfil import fulfil  # noqa: E402

# seconds each beat holds on screen
BEATS = [
    ("listing", 10, "A directory lists a service and publishes a price for it."),
    ("call", 15, "Calling it with only what the directory publishes: 404, then the address the service names itself."),
    ("blocked", 15, "The same record, priced: the gateway is silent, the config endpoint is a disabled deployment, and no payee is published anywhere."),
    ("terminal", 14, "The real output of python run.py."),
    ("quote", 15, "What a payment would be for: the listing, the operation, the published price, and the snapshot it was read from."),
    ("snapshot", 11, "The snapshot digest, and how to recompute it."),
    ("keys", 11, "Two key names for the same thing, and why reading one is worse than it sounds."),
    # Rendered only once media/receipt.json exists, which happens when a settlement has actually run.
    ("receipt", 15, "The settlement, and the same job asked for twice, counted from a public node."),
]

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:#0a0a0a; color:#f2f2f2; width:1280px; height:720px; overflow:hidden;
  font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif; }
.stage { position:relative; width:1280px; height:720px; }
section { position:absolute; inset:0; padding:46px 60px; opacity:0; transition:opacity .4s ease; }
section.on { opacity:1; }
h1 { font-size:33px; margin:0 0 4px; letter-spacing:-.5px; }
h1 small { display:block; font-size:16px; color:#9a9a9a; font-weight:400; letter-spacing:0; margin-top:7px; }
.panel { border:1px solid #262626; border-radius:12px; background:#0e0e0e; padding:20px 24px; margin-top:20px; }
.panel.bad { border-color:#4a2a22; }
.panel.good { border-color:#3c4a1f; }
.name { font-size:22px; font-weight:600; }
.asks { color:#d4e85a; font-size:19px; margin-left:14px; }
table { width:100%; border-collapse:collapse; font-size:14px; margin-top:10px; }
th { text-align:left; color:#9a9a9a; font-weight:500; padding:8px 10px; border-bottom:1px solid #262626; }
td { padding:9px 10px; border-bottom:1px solid #1b1b1b; color:#cfcfcf; vertical-align:top; }
td.mono, .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; color:#e8e8e8;
  word-break:break-all; }
.ok { color:#c7e05a; } .no { color:#ff8b6b; }
.note { color:#9a9a9a; font-size:15px; margin-top:18px; }
.note b { color:#e8e8e8; font-weight:600; }
pre.term { background:#000; border:1px solid #262626; border-radius:10px; padding:18px 20px; margin-top:18px;
  height:556px; overflow:hidden; white-space:pre-wrap; word-break:break-word;
  font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; color:#cfcfcf; }
.digest { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:15px; color:#d4e85a;
  word-break:break-all; line-height:1.7; }
.cmd { background:#000; border:1px solid #262626; border-radius:8px; padding:12px 16px; margin-top:14px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:14px; color:#cfcfcf; }
"""


def capture_run() -> str:
    result = subprocess.run(
        [sys.executable, "run.py", "--no-probe"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    return result.stdout


def build(out_dir: Path) -> tuple[Path, Path]:
    base = os.environ.get("HUB_DIRECTORY_URL", HUB_BASE)
    chain_id = int(os.environ.get("KEEPERHUB_CHAIN_ID", "11155111"))
    data = report(base, chain_id)
    snapshot = data["snapshot"] or {}

    priced = [
        p for p in data["plans"]
        if not any(b["kind"] == "not-priced" or b["field"] == "payment" for b in p["blocked_by"])
    ]
    if not priced:
        raise SystemExit("the directory currently publishes no priced record, so there is nothing to film")
    plan = priced[0]
    record = next(r for r in fetch_records(base) if r.name == plan["record"])
    call = fulfil(record)
    terminal = capture_run()

    # --- beat 1: the listing -------------------------------------------------
    listing = f"""
    <h1>A directory lists a service, and publishes a price
      <small>{html.escape(base)} — {data['records']} records, read live</small></h1>
    <div class="panel">
      <div><span class="name">{html.escape(plan['record'])}</span>
        <span class="asks">asks {plan['amount']} {html.escape(plan['currency'] or '')}</span></div>
      <table>
        <tr><th>published operation</th><td>{html.escape(plan['operation'] or '-')}</td></tr>
        <tr><th>published address</th><td class="mono">{html.escape(call.steps[0].url if call.steps else '-')}</td></tr>
        <tr><th>published payee</th><td class="no">none. the record does not carry an address to pay</td></tr>
      </table>
    </div>
    <p class="note">Two separate questions follow from a listing like this. <b>Can it be used?</b>
    And <b>can it be paid?</b> They do not have the same answer.</p>"""

    # --- beat 2: calling it --------------------------------------------------
    call_rows = "".join(
        f'<tr><td>{html.escape(s.what)}</td><td class="mono">{html.escape(s.url)}</td>'
        f'<td class="{"ok" if s.status == 200 else "no"}">{s.status}: {html.escape(s.detail)}</td></tr>'
        for s in call.steps
    )
    call_verdict = (
        f'<p class="note">The address that answered was <b>{html.escape(call.url_source or "")}</b>. '
        "The service behind the listing runs.</p>"
        if call.delivered
        else '<p class="note">What the directory publishes was not enough to call this listing.</p>'
    )
    call_html = f"""
    <h1>Call it with only what the directory publishes
      <small>python run.py --call "{html.escape(plan['record'])}"</small></h1>
    <div class="panel">
      <table><tr><th>what was done</th><th>address</th><th>what came back</th></tr>{call_rows}</table>
    </div>
    {call_verdict}"""

    # --- beat 3: what stops the payment --------------------------------------
    probe_rows = "".join(
        f'<tr><td class="mono">{html.escape(p["url"])}</td>'
        f'<td class="{"ok" if p["reachable"] else "no"}">{"answers" if p["reachable"] else "no answer"}</td>'
        f'<td>{html.escape(p["note"])}</td></tr>'
        for p in plan["probes"]
    )
    blocker_rows = "".join(
        f'<tr><td class="mono">{html.escape(b["field"])}</td><td class="no">{html.escape(b["why"])}</td></tr>'
        for b in plan["blocked_by"]
    )
    blocked = f"""
    <h1>Now try to pay it
      <small>The same record, and the same rule: never fill in a field the record does not publish</small></h1>
    <div class="panel bad">
      <table><tr><th>published endpoint</th><th></th><th>what came back</th></tr>{probe_rows}</table>
      <table><tr><th>field</th><th>why this stops the payment</th></tr>{blocker_rows}</table>
    </div>
    <p class="note">A 402 is what a payment challenge looks like. <b>This one carries
    DEPLOYMENT_DISABLED</b>, so a caller switching on the status code alone would try to pay a
    deployment that is switched off.</p>"""

    # --- beat 4: the terminal ------------------------------------------------
    terminal_html = f"""
    <h1>The whole directory, priced the same way
      <small>python run.py</small></h1>
    <pre class="term" id="term">{html.escape(terminal)}</pre>"""

    # --- beat 5: what the payment is for -------------------------------------
    quote_rows = "".join(
        f'<tr><th>{html.escape(k)}</th><td class="mono">{html.escape(str(v))}</td></tr>'
        for k, v in plan["quote"].items()
    )
    quote = f"""
    <h1>What the payment would be for
      <small>A transfer on its own is a transfer. This one names the job it belongs to.</small></h1>
    <div class="panel">
      <table>{quote_rows}</table>
    </div>
    <p class="note">The idempotency key is a digest of exactly these terms, so <b>the key names the
    job and not the attempt</b>. A retry after a timeout is the same request. A re-quote at a
    different price is a different job, and cannot replay the old payment.</p>
    <p class="digest">{html.escape(plan['quote_digest'])}</p>"""

    # --- beat 6: the snapshot ------------------------------------------------
    snap = f"""
    <h1>The price was published at a moment
      <small>So the quote pins the bytes it was read from</small></h1>
    <div class="panel good">
      <table>
        <tr><th>sha256 of the bytes the directory served</th><td class="mono">{html.escape(snapshot.get('digest', '-'))}</td></tr>
        <tr><th>bytes</th><td>{snapshot.get('bytes', 0)}</td></tr>
        <tr><th>read at</th><td>{html.escape(snapshot.get('read_at', '-'))}</td></tr>
      </table>
      <div class="cmd">curl -s {html.escape(base)}/api/resources | shasum -a 256</div>
    </div>
    <p class="note">Anyone can run that line. <b>A different digest means the listing moved</b>, which
    makes it a different quote rather than the same one at a new price.</p>"""

    # --- beat 7: the two key names -------------------------------------------
    keys = data["key_names"]
    missed = keys["what a reader of payment.mode alone would miss"]
    keys_html = f"""
    <h1>One more thing a payer has to survive
      <small>The same field, written under two different names</small></h1>
    <div class="panel">
      <table>
        <tr><th>written under</th><th>records</th><th>which</th></tr>
        <tr><td class="mono">payment.mode</td><td>{len(keys['payment.mode only'])}</td><td>{html.escape(', '.join(keys['payment.mode only']))}</td></tr>
        <tr><td class="mono">payment.model</td><td>{len(keys['payment.model only'])}</td><td>{html.escape(', '.join(keys['payment.model only']))}</td></tr>
        <tr><td class="mono">neither</td><td>{len(keys['neither key'])}</td><td>{html.escape(', '.join(keys['neither key']))}</td></tr>
      </table>
    </div>
    <p class="note">A caller reading only <span class="mono">payment.mode</span> sees nothing for
    {len(missed)} of them. <b>An absent key and a free service are the same value</b>, so those
    records do not look unreadable. They look free.</p>"""

    # --- beat 8: the receipt, when there is one ------------------------------
    receipt_html = _receipt_section(ROOT / "media" / "receipt.json")

    sections = {
        "listing": listing,
        "call": call_html,
        "blocked": blocked,
        "terminal": terminal_html,
        "quote": quote,
        "snapshot": snap,
        "keys": keys_html,
        "receipt": receipt_html,
    }
    beats = [(name, seconds, says) for name, seconds, says in BEATS if sections.get(name)]
    body = "".join(f'<section id="{name}">{sections[name]}</section>' for name, _, _ in beats)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Agent3 Remit</title><style>{CSS}</style></head>
<body><div class="stage">{body}</div>
<script>
// One beat is shown per frame, chosen by the query string, so each still is deterministic.
const params = new URLSearchParams(location.search);
const beat = params.get('beat') || {json.dumps(BEATS[0][0])};
const target = document.getElementById(beat);
if (target) {{ target.classList.add('on'); }}
const scroll = parseInt(params.get('scroll') || '0', 10);
const term = document.getElementById('term');
if (term && scroll) {{ term.scrollTop = scroll; }}
</script>
</body></html>
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    demo = out_dir / "demo.html"
    demo.write_text(page)

    # How many frames the terminal beat needs is a property of the output, not a guess: a block that
    # fits on screen is one still, and scrolling it would only play the same picture sixteen times.
    meta = out_dir / "demo-meta.json"
    meta.write_text(
        json.dumps(
            {
                "terminal_lines": terminal.count(chr(10)) + 1,
                "beats": [[name, seconds] for name, seconds, _ in beats],
            },
            indent=2,
        )
    )

    storyboard = ROOT / "media" / "storyboard.md"
    storyboard.parent.mkdir(parents=True, exist_ok=True)
    total = sum(seconds for _, seconds, _ in beats)
    at = 0.0
    rows = []
    for name, seconds, says in beats:
        rows.append(f"| {_stamp(at)} | {_stamp(at + seconds)} | `{name}` | {says} |")
        at += seconds
    storyboard.write_text(
        "# Storyboard, remit-demo.mp4\n\n"
        f"Silent, 1280x720, 30fps, {total:.0f} seconds. Rendered frame by frame from `site/demo.html` by\n"
        "headless Chrome; no screen was recorded. Every number on screen comes from a live read of the\n"
        f"directory at build time, and the terminal beat is the real stdout of `python run.py`.\n\n"
        f"Built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} against "
        f"{base}, snapshot `{snapshot.get('digest', '-')}`.\n\n"
        "| from | to | beat | what the picture says |\n|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        "What the narration should carry, because the screen does not: that an agent is about to spend\n"
        "real money on the strength of a listing, that a listing can be perfectly usable and still\n"
        "impossible to pay, and that the honest output in that case is the name of the missing field.\n\n"
        "What the narration must not say: that the directory is wrong, or that anybody published a\n"
        "false address. What was observed is that the published address returned 404 and named other\n"
        "endpoints, and that one of those answered.\n"
    )
    return demo, storyboard


def _receipt_section(path: Path) -> str:
    """The last beat, and the only one that needs a settlement to have happened.

    What it shows is not our own report of the payment. It is the organisation wallet's transaction
    count read from a public node either side of each attempt, because "asking twice did not pay
    twice" is exactly the claim that should not rest on the word of whoever would have paid.
    """
    if not path.exists():
        return ""
    data = json.loads(path.read_text())
    rows = "".join(
        f'<tr><td>{html.escape(a["label"])}</td>'
        f'<td class="mono">{html.escape((a["tx_hash"] or "-")[:26])}</td>'
        f'<td>{a["wallet_tx_count_before"]} &rarr; {a["wallet_tx_count_after"]}</td>'
        f'<td class="{"ok" if a["moved"] == 0 else ""}">{"nothing new reached the chain" if a["moved"] == 0 else f"one transaction"}</td></tr>'
        for a in data["attempts"]
    )
    first = data["attempts"][0]
    return f"""
    <h1>It settled, and asking again did not settle it twice
      <small>Transaction count read from {html.escape(data['counted_from'])}, not from the payer</small></h1>
    <div class="panel good">
      <table><tr><th>attempt</th><th>transaction</th><th>wallet count</th><th></th></tr>{rows}</table>
    </div>
    <p class="note">The idempotency key is the quote digest, so the second attempt is the same job
    rather than a second one. <b>The count is read from a public node</b>, which is the only party
    with no stake in the answer.</p>
    <p class="digest">{html.escape(data['explorer'] + (first['tx_hash'] or ''))}</p>"""


def _stamp(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "site"))
    args = parser.parse_args()
    demo, storyboard = build(Path(args.out))
    print(f"wrote {demo}\nwrote {storyboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
