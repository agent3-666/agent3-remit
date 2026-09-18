"""Call a listing the way a stranger would, using only what the directory publishes.

This is the other half of the story. Settlement answers "can this be paid". This answers "can this be
used at all", and the two together cover the path from finding something in a directory to having its
output in your hand.

The rule is the same one settlement follows: use what is published, and when that is not enough, say
what was missing rather than reaching for knowledge the directory never gave. Where a call only
succeeds because the service itself volunteered a correction, that is recorded as coming from the
service, not from the listing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .directory import Record


@dataclass
class Step:
    what: str
    url: str
    status: int | None
    detail: str


@dataclass
class Fulfilment:
    record_name: str
    resource_id: str
    operation: str | None
    called_url: str | None = None
    delivered: bool = False
    result_preview: str | None = None
    url_source: str | None = None
    steps: list[Step] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "record": self.record_name,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "delivered": self.delivered,
            "called_url": self.called_url,
            "url_source": self.url_source,
            "result_preview": self.result_preview,
            "missing": self.missing,
            "steps": [{"what": s.what, "url": s.url, "status": s.status, "detail": s.detail} for s in self.steps],
        }


def _get(url: str, timeout: int = 20) -> tuple[int | None, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "agent3-remit/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4000).decode(errors="replace")
            return response.status, body, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        body = ""
        try:
            body = err.read(4000).decode(errors="replace")
        except Exception:
            pass
        return err.code, body, err.headers.get("Content-Type", "") if err.headers else ""
    except Exception as err:
        return None, str(err), ""


def _endpoints_offered(body: str) -> list[str]:
    """Some services answer an unknown path by naming the paths they do have. That is a correction
    from the service, and it is worth using, as long as it is labelled as such."""
    try:
        parsed = json.loads(body)
    except Exception:
        return []
    available = parsed.get("available") if isinstance(parsed, dict) else None
    return [str(item) for item in available] if isinstance(available, list) else []


def fulfil(record: Record, query: str = "keeperhub", operation: str | None = None, follow_correction: bool = True) -> Fulfilment:
    result = Fulfilment(
        record_name=record.name,
        resource_id=record.resource_id,
        operation=operation or (record.operations[0].name if record.operations else None),
    )

    urls = record.callable_urls
    if not urls:
        result.missing.append("interfaces: no published interface names a host, so there is nothing to call")
        return result

    published = urls[0]
    # The record names operations but publishes no method or path for them, so the only address a
    # stranger has is the interface URL itself.
    if not any(op.has_http_binding for op in record.operations):
        result.missing.append(
            "operations: the record names operations but publishes no path or method for any of them, "
            "so which URL serves which operation is not stated"
        )

    status, body, _ = _get(_with_query(published, query))
    result.steps.append(Step("call the published interface URL", published, status, _summarise(body)))

    if status == 200:
        result.delivered = True
        result.called_url = _with_query(published, query)
        result.url_source = "published by the directory"
        result.result_preview = _summarise(body)
        return result

    offered = _endpoints_offered(body)
    if not offered or not follow_correction:
        result.missing.append(
            f"the published interface URL answered {status}, and the record offers no other address to try"
        )
        return result

    # The service named its own endpoints. Use the first, and record where that knowledge came from.
    base = published.rsplit("/", 1)[0] if not published.endswith("/") else published[:-1]
    candidate = _with_query(f"{base}/{offered[0]}", query)
    status2, body2, _ = _get(candidate)
    result.steps.append(
        Step(
            "the service named its own endpoints, so try the first one it offered",
            candidate,
            status2,
            f"offered {offered}; " + _summarise(body2),
        )
    )
    if status2 == 200:
        result.delivered = True
        result.called_url = candidate
        result.url_source = "corrected by the service itself, not published by the directory"
        result.result_preview = _summarise(body2)
    else:
        result.missing.append(f"the corrected address answered {status2} as well")
    return result


def _with_query(url: str, query: str) -> str:
    # ASSUMPTION(the record does not publish parameter names): `q` is used because the service asks
    # for it by name in its own error response. It is not stated anywhere in the directory record.
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}q={urllib.parse.quote(query)}"


def _summarise(body: str) -> str:
    text = body.strip()
    if text.startswith("<"):
        return f"an HTML page ({len(body)} bytes), not an API response"
    try:
        parsed = json.loads(text)
    except Exception:
        return text[:160]
    if isinstance(parsed, dict) and parsed.get("error"):
        return f"error: {parsed['error']}"
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        organic = parsed["data"].get("organic")
        if isinstance(organic, list) and organic:
            first = organic[0]
            return f"{len(organic)} results, first: {first.get('title', '')[:70]} — {first.get('link', '')[:60]}"
    return json.dumps(parsed)[:160]
