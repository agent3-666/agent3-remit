# Agent3 Remit

**Turn a published quote into money that can actually move. When the published record is not enough
to pay anyone, name the field that is missing, and never guess it.**

A directory can advertise a price without being able to take the money. Agent3 Remit reads a live
directory, works out whether each priced record can actually be paid, and settles the ones that can
through [KeeperHub](https://keeperhub.com) — dry run, one broadcast, receipt read back from the chain.
For the ones that cannot, it says which field is missing instead of filling in something plausible.

```bash
python run.py                    # read the live directory and price every record
python run.py --json
python run.py --pay "<record>" --payee 0x...   # settle through KeeperHub
python run.py --call "<record>" --query keeperhub   # call it with only what the directory publishes
```

Nothing to install: it runs on a stock Python 3.9+ with only the standard library.

## The live project it integrates with

[Agent3 Hub](https://a2a-hub-chi.vercel.app) is a running A2A/MCP directory. Remit reads it over its
public API and **never writes to it**; it sits alongside the Hub rather than inside it.

The Hub is small, and this is stated plainly rather than dressed up: **9 resource records**. That size
is what makes the integration checkable — every conclusion below can be verified by hand in a couple
of minutes.

What reading it live turns up:

| What the directory does | Why it stops a payment |
|---|---|
| Payment terms are written under two different key names: 5 records use `payment.mode`, 3 use `payment.model`, 1 uses neither | A caller that reads one key is blind to the others. An absent key and a free service are the same value, so unreadable records look free |
| The one priced record advertises 0.01 USD | Its settlement gateway does not answer, and its config endpoint returns 402 from a disabled deployment |
| That 402 carries `DEPLOYMENT_DISABLED` | A caller that reads only the status code takes it for an x402 payment challenge and tries to pay |
| Nowhere in the record is there a payee address | So there is no honest way to pay it. The answer is to say so, not to invent an address |

**The result: the directory can quote a price and cannot take the money.** That is the gap KeeperHub
fills, and it is a named, reproducible failure rather than a general claim about agent commerce.

### Calling the same listing

`--call` uses only the addresses the directory publishes. Three observations, in order, for the one
priced record:

1. The directory publishes `https://agent3-x-api.vercel.app/api/v1/google/paid`.
2. That address returns **404 `Unknown endpoint`**, and names the endpoints it does serve:
   `search`, `news`, `images`, `places`.
3. Calling the first of those returns **200** with seven results.

So the service runs. The run is recorded as **corrected by the service itself, not published by the
directory**, because the address that worked did not come from the listing. The free Google record
goes no further: its published address answers with an HTML 404 and offers no correction.

### Every quote is pinned to a snapshot, and anyone can recompute it

A price is published at a moment, so the plan records the `sha256` of the exact bytes the directory
served, the byte count, and the time of the read. At the time of writing that is
`ede78348d812414a8014f007ae3ce4ad9bcb7186f563fe94c5d51951809e48b2` over 127,185 bytes. Recompute it
with `curl -s https://a2a-hub-chi.vercel.app/api/resources | shasum -a 256`. The digest names that one
reading, so a different digest later is the mechanism doing its job: the listing has changed, which
makes it a different quote, and the price agreed against the old one does not carry over.

## What it does with KeeperHub

`src/remit/keeperhub.py` is a Direct Execution client written against
[the published API](https://docs.keeperhub.com/api/direct-execution). The order is fixed:

1. **Dry run** (`simulate: true`, a boolean). It creates no execution record.
2. **Broadcast once**, under an `Idempotency-Key` derived from the job and never from the attempt.
   The job is the quote: resource id, operation, the price exactly as published, and the directory
   snapshot it was read from. A retry after a timeout is therefore the same request, and KeeperHub
   replays the original result rather than paying twice. A re-quote at a different price is a
   different job, so the new price cannot silently replay the old payment.
3. **Read the receipt back** by polling `/api/execute/{id}/status` until it is terminal, honouring the
   `X-Poll-Interval-Hint` header.

Chain: Ethereum Sepolia, `chainId` 11155111. `GET https://app.keeperhub.com/api/chains` lists 24
chains, 12 of them testnets, with Sepolia enabled.

**No API key means no settlement.** In that state the service reports that it cannot settle and sends
nothing. It never reports a payment that did not happen.

## Tests, and why they mean something

```bash
python -m pytest tests -q          # 24 tests
python scripts/mutation_check.py   # delete each rule, its test must fail
```

The KeeperHub client is tested against a **stub server that records what it was actually sent**, not
by reading our own source. Those tests assert the dry run comes first, that only the broadcast carries
an idempotency key, that the receipt is polled rather than assumed, that asking twice for the same job
produces one execution, and that with no key configured **zero requests leave the process**.

Calling a listing is tested the same way: a stub service reproduces the three shapes that matter, an
address that works, an address that fails while naming the endpoints it does have, and an address
that fails silently. The test asserts that a call which only succeeded after a correction is not
credited to the directory.

`scripts/mutation_check.py` deletes each rule's condition one at a time and requires the test covering
it to fail. It refuses two results that look like success: a test filter that matched nothing, and a
mutant that failed to import. Seven rules, seven failing tests.

## What this does not claim

Each liveness probe is **one request from one machine**. A host that does not answer here may answer
elsewhere, and nothing here says otherwise. What makes the reading worth something is that the probes
run together: when one endpoint is silent while the directory's own host and other endpoints answer in
the same moment, the silence is about that endpoint rather than the network here.

When a payee address is supplied on the command line, the plan records it as **supplied by the caller,
not by the directory**, because the directory does not publish one.

## Layout

```
run.py                      read, price, settle
src/remit/directory.py      reads the live Hub directory, both payment key names, with a cache
src/remit/settlement.py     builds a payment plan, or names the missing fields
src/remit/keeperhub.py      Direct Execution client: dry run, one broadcast, receipt
src/remit/fulfil.py         calls a listing using only the addresses the directory publishes
scripts/build_site.py       renders the page from the same pipeline
scripts/mutation_check.py   removes each rule and requires its test to fail
```
