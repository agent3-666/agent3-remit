"""Reads the live Agent3 Hub directory, and reports what it actually publishes.

Read-only, always. The Hub is somebody's running production service; this service sits alongside it
and never writes to it.

Two things this module refuses to do, because both of them hide the problem it exists to show:

  * pick a winner between the two payment key names. Records use `mode` and others use `model`. A
    reader that knows only one key sees nothing for the other records, and "key absent" looks exactly
    like "this is free". Both are read, both are reported.
  * fill in a plausible default for a field the record does not publish. A guessed payment looks
    identical to a real one until the money is gone.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

HUB_BASE = "https://a2a-hub-chi.vercel.app"
USER_AGENT = "agent3-remit/1.0 (+https://github.com/agent3-666)"

# One upstream read serves every caller for this long, so a judge clicking around costs the Hub
# nothing and costs us nothing.
CACHE_SECONDS = 300

_cache: dict[str, tuple[float, Any]] = {}


def _get_json(url: str, timeout: int = 25) -> Any:
    hit = _cache.get(url)
    now = time.time()
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode())
    _snapshots[url] = Snapshot(
        digest=hashlib.sha256(raw).hexdigest(),
        read_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        bytes_read=len(raw),
        url=url,
    )
    _cache[url] = (now, payload)
    return payload


@dataclass(frozen=True)
class Snapshot:
    """What the directory said, and when, fixed to a digest.

    A settlement quotes a price that was published at a moment. Without pinning that moment, a
    receipt says money moved but not what it was for: the listing could have changed since. The
    digest is over the exact bytes the directory served.
    """

    digest: str
    read_at: str
    bytes_read: int
    url: str

    def as_dict(self) -> dict:
        return {"digest": self.digest, "read_at": self.read_at, "bytes": self.bytes_read, "url": self.url}


_snapshots: dict[str, Snapshot] = {}


def snapshot_of(base: str = HUB_BASE) -> Snapshot | None:
    """The snapshot the current records were read from."""
    return _snapshots.get(f"{base}/api/resources")


def cache_state() -> dict:
    return {url: {"age_seconds": round(time.time() - at, 1)} for url, (at, _) in _cache.items()}


@dataclass
class PaymentReading:
    """What a record says about payment, read through both key names at once."""

    raw: dict | None
    mode_key: str | None = None  # value under "mode", if the record uses that key
    model_key: str | None = None  # value under "model", if the record uses that key
    amount: float | None = None
    currency: str | None = None
    gateway: str | None = None
    config_url: str | None = None
    supports_x402: bool | None = None

    @property
    def keys_present(self) -> list[str]:
        present = []
        if self.mode_key is not None:
            present.append("payment.mode")
        if self.model_key is not None:
            present.append("payment.model")
        return present

    @property
    def disagreement(self) -> str | None:
        """Says so when the two key names carry different values, instead of choosing one."""
        if self.mode_key is not None and self.model_key is not None and self.mode_key != self.model_key:
            return f"payment.mode says {self.mode_key!r} and payment.model says {self.model_key!r}"
        return None

    @property
    def looks_priced(self) -> bool:
        if self.amount is not None and self.amount > 0:
            return True
        return (self.mode_key or self.model_key) not in (None, "free")


@dataclass
class Operation:
    name: str
    estimated_cost: Any = None
    has_http_binding: bool = False


@dataclass
class Record:
    resource_id: str
    name: str
    provider: str | None
    payment: PaymentReading
    operations: list[Operation] = field(default_factory=list)
    interfaces: list[dict] = field(default_factory=list)

    @property
    def callable_urls(self) -> list[str]:
        """Interface URLs that name a host. A relative path cannot be called by a stranger."""
        out = []
        for interface in self.interfaces:
            url = str(interface.get("url", ""))
            if url.startswith("http://") or url.startswith("https://"):
                out.append(url)
        return out

    @property
    def uncallable_reason(self) -> str | None:
        if not self.interfaces:
            return "the record publishes no interface at all"
        if not self.callable_urls:
            urls = [str(i.get("url", "")) for i in self.interfaces]
            return f"no interface names a host; published url(s): {', '.join(u or '(empty)' for u in urls)}"
        return None


def read_payment(raw: dict | None) -> PaymentReading:
    if not isinstance(raw, dict):
        return PaymentReading(raw=raw)
    model_key = None
    model_key = raw.get("model")  # GUARD:read-both-key-names
    return PaymentReading(
        raw=raw,
        mode_key=raw.get("mode"),
        model_key=model_key,
        amount=raw.get("amount"),
        currency=raw.get("currency"),
        gateway=raw.get("gateway"),
        config_url=raw.get("configUrl"),
        supports_x402=raw.get("supportsX402"),
    )


def fetch_records(base: str = HUB_BASE) -> list[Record]:
    payload = _get_json(f"{base}/api/resources")
    items = payload if isinstance(payload, list) else payload.get("data") or payload.get("resources") or []
    records = []
    for item in items:
        operations = [
            Operation(
                name=str(op.get("name") or op.get("operationId") or "unnamed"),
                estimated_cost=op.get("estimatedCost"),
                has_http_binding=bool(op.get("http") or op.get("method") or op.get("path")),
            )
            for op in (item.get("operations") or [])
        ]
        records.append(
            Record(
                resource_id=str(item.get("resource_id") or item.get("id") or ""),
                name=str(item.get("name") or "unnamed"),
                provider=(item.get("provider") or {}).get("name") if isinstance(item.get("provider"), dict) else item.get("provider"),
                payment=read_payment(item.get("payment")),
                operations=operations,
                interfaces=list(item.get("interfaces") or []),
            )
        )
    return records


def key_name_summary(records: list[Record]) -> dict:
    """How the directory splits across the two key names, counted rather than asserted."""
    mode_only = [r.name for r in records if r.payment.mode_key is not None and r.payment.model_key is None]
    model_only = [r.name for r in records if r.payment.model_key is not None and r.payment.mode_key is None]
    both = [r.name for r in records if r.payment.mode_key is not None and r.payment.model_key is not None]
    neither = [r.name for r in records if r.payment.mode_key is None and r.payment.model_key is None]
    return {
        "total": len(records),
        "payment.mode only": mode_only,
        "payment.model only": model_only,
        "both keys": both,
        "neither key": neither,
        "what a reader of payment.mode alone would miss": model_only + neither,
        "what a reader of payment.model alone would miss": mode_only + neither,
    }
