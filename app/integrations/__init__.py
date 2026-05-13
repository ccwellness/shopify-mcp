"""Third-party integration adapters — out-of-process REST/SOAP clients.

L1 adapters: each module wraps one external API (OrderGroove, GA4, etc.)
behind a small typed client. Domain models / services / blueprints never
import these directly — they go through a provider in `app.services.*`
that owns the protocol contract.
"""

from __future__ import annotations
