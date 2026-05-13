"""BulkOperationsClient — TR-10.

Shopify's Bulk Operations API lets a single GraphQL query stream millions
of rows out as JSONL without paying paginated query costs. The flow:

  1. POST `bulkOperationRunQuery(query: ...)` mutation. Shopify enqueues
     the job server-side and returns a bulk operation id.
  2. Poll `currentBulkOperation` until status=COMPLETED (or FAILED, etc.).
  3. The COMPLETED response includes a signed `url` for the JSONL output.
  4. GET that URL (HTTP, no auth header) and stream the bytes — the URL
     expires in ~6 hours, so consumers process eagerly.

Shopify enforces "one bulk op per shop at a time"; if a job is already
running we surface the userError so the caller can decide.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from app.shopify.client import ShopifyClient
from app.shopify.errors import ShopifyError, ShopifyGraphQLError

_log = logging.getLogger(__name__)
_DOWNLOAD_MAX_ATTEMPTS = 4  # 1 initial + 3 retries — handles flaky long-running streams
_DOWNLOAD_BACKOFF_SECONDS = 3.0


class BulkOperationStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    CANCELING = "CANCELING"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True, kw_only=True)
class BulkOperationResult:
    operation_id: str
    status: BulkOperationStatus
    object_count: int
    file_size: int
    url: str | None
    error_code: str | None


class BulkOperationError(ShopifyError):
    """Bulk job failed, was canceled, or response was malformed."""


_RUN_BULK = """
mutation RunBulk($query: String!) {
  bulkOperationRunQuery(query: $query) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""

_CURRENT_BULK = """
{
  currentBulkOperation {
    id
    status
    errorCode
    objectCount
    fileSize
    url
  }
}
"""


class BulkOperationsClient:
    """Per-store bulk operations runner."""

    def __init__(
        self,
        client: ShopifyClient,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self._client = client
        # Separate http client for downloading JSONL (no auth header, no retry chain).
        self._download_http = http or httpx.Client(timeout=httpx.Timeout(60.0, read=600.0))

    # -----------------------------------------------------------------------
    # Submit + poll
    # -----------------------------------------------------------------------

    def submit(self, store_key: str, query: str) -> str:
        """POST `bulkOperationRunQuery`. Returns the bulk operation id."""
        data = self._client.query(
            store_key,
            _RUN_BULK,
            variables={"query": query},
            allow_mutation=True,
        )
        body = data.get("bulkOperationRunQuery") or {}
        errors = body.get("userErrors") or []
        if errors:
            raise BulkOperationError(f"bulkOperationRunQuery userErrors: {errors}")
        op = body.get("bulkOperation") or {}
        op_id = op.get("id")
        if not op_id:
            raise BulkOperationError(f"bulkOperationRunQuery returned no operation id: {body!r}")
        return str(op_id)

    def current_operation(self, store_key: str) -> BulkOperationResult | None:
        """Return the active or last-completed bulk operation, or None."""
        data = self._client.query(store_key, _CURRENT_BULK)
        op = data.get("currentBulkOperation")
        if op is None:
            return None
        return _parse_bulk_op(op)

    def wait_until_done(
        self,
        store_key: str,
        *,
        max_wait_seconds: int = 1800,
        interval_seconds: int = 5,
    ) -> BulkOperationResult:
        """Poll until status leaves CREATED/RUNNING. Raises on timeout."""
        deadline = time.monotonic() + max_wait_seconds
        last: BulkOperationResult | None = None
        while time.monotonic() < deadline:
            last = self.current_operation(store_key)
            if last is None:
                # Operation rolled out of the "current" slot — treat as done-but-empty.
                raise BulkOperationError("currentBulkOperation returned null mid-poll")
            if last.status not in (BulkOperationStatus.CREATED, BulkOperationStatus.RUNNING):
                return last
            time.sleep(interval_seconds)
        raise BulkOperationError(
            f"bulk operation did not complete within {max_wait_seconds}s "
            f"(last status: {last.status if last else '<unknown>'})"
        )

    # -----------------------------------------------------------------------
    # Download
    # -----------------------------------------------------------------------

    def download_jsonl(self, url: str) -> Iterator[bytes]:
        """Stream the JSONL output line-by-line. Yields raw bytes per line.

        Shopify's bulk-op URLs point at S3 and the connection can be cut
        mid-stream on large downloads (we've observed truncation at ~85 MB
        of a 95 MB file with `httpx.RemoteProtocolError: peer closed
        connection`). Retry the whole download up to `_DOWNLOAD_MAX_ATTEMPTS`
        times on transport errors — the URL is stable for ~6 hours so
        re-requesting is safe, and the consumer side replaces records
        idempotently via the GID unique constraint.
        """
        last_error: Exception | None = None
        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                yield from self._download_once(url)
                return
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                last_error = exc
                if attempt < _DOWNLOAD_MAX_ATTEMPTS:
                    backoff = _DOWNLOAD_BACKOFF_SECONDS * attempt
                    _log.warning(
                        "bulk JSONL download truncated (attempt %d/%d): %s — retrying in %.1fs",
                        attempt,
                        _DOWNLOAD_MAX_ATTEMPTS,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)
        raise BulkOperationError(
            f"bulk JSONL download failed after {_DOWNLOAD_MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    def _download_once(self, url: str) -> Iterator[bytes]:
        with self._download_http.stream("GET", url) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                # iter_lines yields str by default in newer httpx; normalize to bytes.
                if isinstance(raw_line, str):
                    yield raw_line.encode("utf-8")
                else:
                    yield raw_line

    # -----------------------------------------------------------------------
    # Convenience
    # -----------------------------------------------------------------------

    def run_and_collect(
        self,
        store_key: str,
        query: str,
        *,
        max_wait_seconds: int = 1800,
        interval_seconds: int = 5,
    ) -> Iterator[bytes]:
        """Submit + wait + download. Yields raw JSONL lines as bytes."""
        self.submit(store_key, query)
        result = self.wait_until_done(
            store_key,
            max_wait_seconds=max_wait_seconds,
            interval_seconds=interval_seconds,
        )
        if result.status != BulkOperationStatus.COMPLETED:
            raise BulkOperationError(
                f"bulk operation ended with status={result.status} errorCode={result.error_code!r}"
            )
        if not result.url:
            # COMPLETED with no rows — yield nothing.
            return
        yield from self.download_jsonl(result.url)

    def close(self) -> None:
        self._download_http.close()


def _parse_bulk_op(op: dict[str, Any]) -> BulkOperationResult:
    try:
        status = BulkOperationStatus(op["status"])
    except (KeyError, ValueError) as exc:
        raise ShopifyGraphQLError([{"message": f"unrecognized bulk status: {op!r}"}]) from exc
    return BulkOperationResult(
        operation_id=str(op.get("id") or ""),
        status=status,
        object_count=int(op.get("objectCount") or 0),
        file_size=int(op.get("fileSize") or 0),
        url=op.get("url"),
        error_code=op.get("errorCode"),
    )
