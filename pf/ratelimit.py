"""Client-side throttle and retry for the Plane API.

Plane allows 60 requests per minute per key and answers 429 with error code
5900 once you cross it. A relations pull is one request per work item, so any
project past ~60 items WILL hit the wall unless the client paces itself.

The throttle is a shared token bucket: every thread takes a slot before it
sends, so raising --workers changes latency, never the request rate. A 429 or a
low X-RateLimit-Remaining pushes the whole bucket forward, so one thread hitting
the wall slows everyone down rather than each thread discovering it alone.
"""
from __future__ import annotations

import threading
import time


class Throttle:
    def __init__(self, per_minute: float = 55.0):
        self.interval = 60.0 / max(1.0, per_minute)
        self._lock = threading.Lock()
        self._next = 0.0
        self.waited = 0.0        # total seconds spent sleeping, for the report
        self.throttled = 0       # times a 429 forced a global pause

    def take(self) -> None:
        """Block until this caller is allowed to send."""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval
        delay = slot - time.monotonic()
        if delay > 0:
            self.waited += delay
            time.sleep(delay)

    def pause(self, seconds: float, count: bool = True) -> None:
        """Hold every thread off for `seconds` -- used on a 429 or when the
        remaining-requests header gets thin."""
        if seconds <= 0:
            return
        with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)
            if count:
                self.throttled += 1


def retry_after(headers, default: float) -> float:
    """Seconds to wait, preferring what the server told us."""
    ra = headers.get("Retry-After") if headers else None
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass
    reset = headers.get("X-RateLimit-Reset") if headers else None
    if reset:
        try:
            # epoch seconds, UTC
            return max(0.0, float(reset) - time.time())
        except ValueError:
            pass
    return default
