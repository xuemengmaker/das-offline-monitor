"""HTTP alarm event delivery for a controlled downstream receiver.

This adapter is intentionally generic.  It demonstrates the monitoring-service to
alarm-platform boundary without claiming a vendor-specific DAS or SCADA protocol.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def deliver_event(callback_url: str, event: dict[str, Any], max_attempts: int = 3) -> dict[str, object]:
    """POST an event as JSON, retrying transient failures a bounded number of times."""
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    last_error: str | None = None
    last_code: int | None = None
    for attempt in range(1, max_attempts + 1):
        request = Request(
            callback_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Event-Id": str(event["event_id"]),
                "X-Event-Type": str(event["event_type"]),
            },
        )
        try:
            with urlopen(request, timeout=2) as response:
                last_code = response.status
            if 200 <= last_code < 300:
                return {"status": "delivered", "attempts": attempt, "response_code": last_code}
            last_error = f"接收端返回 HTTP {last_code}"
        except HTTPError as exc:
            last_code = exc.code
            last_error = f"接收端返回 HTTP {exc.code}"
        except (URLError, TimeoutError, OSError) as exc:
            last_error = f"投递连接失败: {type(exc).__name__}"
    return {"status": "failed", "attempts": max_attempts, "response_code": last_code, "last_error": last_error}
