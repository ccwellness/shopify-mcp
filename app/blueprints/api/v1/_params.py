"""Query-string parsing helpers shared across `/api/v1/*` routes.

Each parser raises `BadRequestError` on malformed input; the route's
errorhandler converts that into a 400 with a helpful JSON body.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TypeVar


class BadRequestError(Exception):
    """Raised by parsers when a query param is malformed.

    Routes register an errorhandler for this exception that returns a 400
    with `{"error": <message>}`.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_int(field: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise BadRequestError(f"{field} must be an integer (got {raw!r})") from exc


def parse_datetime(field: str, raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BadRequestError(f"{field} must be ISO 8601 datetime (got {raw!r})") from exc


def parse_decimal(field: str, raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise BadRequestError(f"{field} must be a decimal number (got {raw!r})") from exc


E = TypeVar("E", bound=Enum)


def parse_enum[E: Enum](field: str, raw: str, enum_cls: type[E]) -> E:
    try:
        return enum_cls(raw)
    except ValueError as exc:
        valid = ", ".join(str(member.value) for member in enum_cls)
        raise BadRequestError(f"{field} must be one of [{valid}] (got {raw!r})") from exc


def first(args: dict[str, list[str]], name: str) -> str | None:
    """Return the first value for `name`, or None if absent/empty."""
    values = args.get(name)
    return values[0] if values else None
