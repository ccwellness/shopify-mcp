"""ORM row classes mirroring the schema in alembic/versions/0001_initial_schema.py.

Rows are SQLAlchemy 2.0 declarative models. They never leave the
repositories layer (TR-22) — repositories map them to/from the frozen
domain dataclasses in `app.domain.models` before returning.
"""

from __future__ import annotations

from app.db.orm.analytics import AnalyticsKpiDayRow, SessionsDayRow
from app.db.orm.api_audit_log import ApiAuditLogRow
from app.db.orm.api_token import ApiTokenRow
from app.db.orm.base import Base
from app.db.orm.customer import CustomerRow
from app.db.orm.inventory import InventoryItemRow, InventoryLevelRow
from app.db.orm.location import LocationRow
from app.db.orm.order import (
    FulfillmentRow,
    OrderLineItemRow,
    OrderRow,
    OrderShippingAddressRow,
)
from app.db.orm.product import ProductRow, VariantRow
from app.db.orm.refund import RefundRow
from app.db.orm.store import StoreRow
from app.db.orm.subscription import SubscriptionContractRow
from app.db.orm.sync_state import SyncStateRowOrm
from app.db.orm.webhook_event import WebhookEventRow

__all__ = [
    "AnalyticsKpiDayRow",
    "ApiAuditLogRow",
    "ApiTokenRow",
    "Base",
    "CustomerRow",
    "FulfillmentRow",
    "InventoryItemRow",
    "InventoryLevelRow",
    "LocationRow",
    "OrderLineItemRow",
    "OrderRow",
    "OrderShippingAddressRow",
    "ProductRow",
    "RefundRow",
    "SessionsDayRow",
    "StoreRow",
    "SubscriptionContractRow",
    "SyncStateRowOrm",
    "VariantRow",
    "WebhookEventRow",
]
