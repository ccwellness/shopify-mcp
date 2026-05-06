"""Concrete SQLAlchemy implementations of the domain repository Protocols.

Layer rules (TR-25):
- This is the only layer (besides `app.db.*`) allowed to import sqlalchemy.
- This is the only layer allowed to import `app.db.*`.
- ORM rows never leave these modules — every public method maps to/from
  the frozen domain dataclasses in `app.domain.models`.
"""
