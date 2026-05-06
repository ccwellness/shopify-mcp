"""Job queue abstraction.

Two implementations:

- `InlineJobQueue` runs the function immediately on the calling thread.
  Used for tests, dev environments without Redis, and the smoke-test
  scripts. Violates TR-12's "real work happens in a worker" rule, so it
  must NOT be used in production paths that handle live Shopify webhook
  deliveries (5s budget).

- `RqJobQueue` enqueues into Redis via RQ. The actual worker is started
  separately with `rq worker`. This is the production-intended impl.
  Skipped at runtime here because Redis isn't installed on the dev box;
  swap it in once `docker compose up redis` is available.

The view code never sees either — it consumes a `JobQueue` Protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class JobQueue(Protocol):
    """Minimal interface every queue impl must satisfy."""

    def enqueue(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Schedule `fn(*args, **kwargs)` for later (or immediate) execution."""


class InlineJobQueue:
    """Runs jobs synchronously. For dev/tests/smoke; not for live webhooks."""

    def enqueue(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        fn(*args, **kwargs)


class RqJobQueue:
    """Real Redis-backed queue. Lazy-imports rq so the dependency is optional at import time."""

    def __init__(self, redis_url: str, *, queue_name: str = "default") -> None:
        # Lazy import — avoids forcing every test to load redis/rq.
        import redis  # noqa: PLC0415
        import rq  # noqa: PLC0415

        self._connection = redis.Redis.from_url(redis_url)
        self._queue = rq.Queue(queue_name, connection=self._connection)

    def enqueue(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self._queue.enqueue(fn, *args, **kwargs)
