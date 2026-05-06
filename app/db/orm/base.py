"""Declarative base for every ORM row class."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single shared metadata for all tables in this app."""
