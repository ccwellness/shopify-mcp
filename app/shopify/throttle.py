"""Cost-aware throttle helper (TR-8).

Shopify GraphQL responses include `extensions.cost.throttleStatus` with the
caller's bucket state. When `currentlyAvailable < LOW_WATER_POINTS`, the
client sleeps long enough for the bucket to refill back up to the low-water
mark, with exponential backoff + jitter so retried THROTTLED queries don't
all wake at the same moment.
"""

from __future__ import annotations

import random
import time
from typing import Any

LOW_WATER_POINTS = 100
"""When `currentlyAvailable` falls below this, sleep before the next call."""


def parse_throttle_status(extensions: dict[str, Any] | None) -> dict[str, float]:
    """Pull the relevant numeric fields out of a Shopify cost block.

    Returns sane defaults if the block is missing — never raises. The
    standard shape from Shopify is:
        {"cost": {"requestedQueryCost": ..., "actualQueryCost": ...,
                  "throttleStatus": {"maximumAvailable": 1000.0,
                                     "currentlyAvailable": 985,
                                     "restoreRate": 50.0}}}
    """
    cost = (extensions or {}).get("cost", {}) or {}
    status = cost.get("throttleStatus") or {}
    return {
        "actual_cost": float(cost.get("actualQueryCost") or 0),
        "currently_available": float(status.get("currentlyAvailable") or 0),
        "restore_rate": float(status.get("restoreRate") or 50),  # default per Shopify docs
        "maximum_available": float(status.get("maximumAvailable") or 1000),
    }


def compute_backoff_seconds(
    currently_available: float,
    restore_rate: float,
    *,
    attempt: int = 0,
    target: int = LOW_WATER_POINTS,
) -> float:
    """How long to sleep before the next call.

    `attempt=0` → minimum sleep needed to refill back up to `target`.
    `attempt>0` → adds an exponential factor so retries spread out.
    Always adds 0–500ms jitter.
    """
    deficit = max(target - currently_available, 0.0)
    base = deficit / max(restore_rate, 1.0)
    backoff = base * (1.5**attempt)
    jitter = random.uniform(0.0, 0.5)  # noqa: S311 — backoff jitter, not security-sensitive
    return backoff + jitter


def sleep_if_low(
    extensions: dict[str, Any] | None,
    *,
    attempt: int = 0,
    sleep_fn: Any = time.sleep,
) -> float:
    """Inspect a response's cost block; sleep if the bucket is low. Returns seconds slept."""
    parsed = parse_throttle_status(extensions)
    if parsed["currently_available"] >= LOW_WATER_POINTS and attempt == 0:
        return 0.0
    seconds = compute_backoff_seconds(
        parsed["currently_available"], parsed["restore_rate"], attempt=attempt
    )
    sleep_fn(seconds)
    return seconds
