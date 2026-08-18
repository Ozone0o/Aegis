"""Structured event bus and bounded event history.

Events are the durable, operator-facing side of Aegis.  Health state is
current truth; events explain how that truth changed and why a recovery was
selected.  The bus performs small-window deduplication so a stale topic does
not produce one identical alarm per timer tick.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .models import Event, HealthStatus

logger = logging.getLogger(__name__)


class EventBus:
    """Publish events to subscribers and retain a bounded in-memory history."""

    def __init__(self, max_events: int = 1000, dedupe_window: float = 60.0) -> None:
        self.max_events = max(1, max_events)
        self.dedupe_window = max(0.0, dedupe_window)
        self._events: deque[Event] = deque(maxlen=self.max_events)
        self._last_emitted: dict[str, float] = {}
        self._subscribers: list[Callable[[Event], None]] = []
        self._lock = threading.RLock()

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, event: Event, dedupe_key: str | None = None) -> Event | None:
        """Publish ``event`` unless the same key was recently emitted.

        ``None`` is returned for a suppressed duplicate. Subscribers are
        called outside the lock, so a notifier cannot hold the event-bus lock.
        Delivery is still synchronous: a slow subscriber can block the caller
        of ``publish``. Applications that need a non-blocking controller loop
        should subscribe through a bounded queue or worker owned by that
        application and define its backpressure policy. Subscriber failures
        are isolated and logged.
        """

        now = event.timestamp
        key = dedupe_key or self._default_dedupe_key(event)
        with self._lock:
            previous = self._last_emitted.get(key)
            if (
                previous is not None
                and self.dedupe_window > 0
                and now - previous < self.dedupe_window
            ):
                return None
            self._last_emitted[key] = now
            self._events.append(event)
            subscribers = tuple(self._subscribers)

        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                logger.exception("Aegis event subscriber failed for %s", event.kind)
        return event

    def emit(
        self,
        kind: str,
        source: str,
        message: str,
        *,
        status: HealthStatus | None = None,
        previous_status: HealthStatus | None = None,
        severity: str = "info",
        root_cause: str | None = None,
        affected: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        timestamp: float | None = None,
    ) -> Event | None:
        event = Event(
            kind=kind,
            source=source,
            message=message,
            status=status,
            previous_status=previous_status,
            severity=severity,
            root_cause=root_cause,
            affected=list(affected),
            metadata=dict(metadata or {}),
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        return self.publish(event, dedupe_key=dedupe_key)

    def recent(self, limit: int | None = None, since: float | None = None) -> list[Event]:
        with self._lock:
            events = list(self._events)
        if since is not None:
            events = [event for event in events if event.timestamp >= since]
        if limit is not None:
            events = events[-max(0, limit) :]
        return events

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_emitted.clear()

    @staticmethod
    def _default_dedupe_key(event: Event) -> str:
        return ":".join(
            [
                event.kind,
                event.source,
                event.status.value if event.status else "",
                event.root_cause or "",
            ]
        )


class JsonEventStore:
    """Append-only JSONL persistence used by ``aegis status/events``.

    Persistence is intentionally best-effort: a full or read-only runtime
    directory must not bring down the health loop.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            logger.exception("Unable to persist Aegis event to %s", self.path)

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open(encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream if line.strip()]
        except (OSError, json.JSONDecodeError):
            logger.exception("Unable to read Aegis event store %s", self.path)
            return []
        if limit is None:
            return rows
        if limit <= 0:
            return []
        return rows[-limit:]


class JsonStateStore:
    """Atomic-ish JSON snapshot persistence for status queries."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, states: dict[str, Any], *, timestamp: float | None = None) -> None:
        payload = {
            "timestamp": timestamp if timestamp is not None else time.time(),
            "states": {
                name: state.to_dict() if hasattr(state, "to_dict") else state
                for name, state in states.items()
            },
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            logger.exception("Unable to persist Aegis state to %s", self.path)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Unable to read Aegis state store %s", self.path)
            return None
