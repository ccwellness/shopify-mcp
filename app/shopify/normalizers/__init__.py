"""Shopify webhook payload → domain dataclass normalizers.

Pure functions (no DB, no I/O). The dispatcher calls a normalizer to turn
the gzip-decompressed JSON into domain objects, then orchestrates the
repository upserts in dependency order (e.g. customer before order).
"""
