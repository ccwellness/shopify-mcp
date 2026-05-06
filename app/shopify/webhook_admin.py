"""Webhook subscription management against Shopify's Admin API.

The connector subscribes to a fixed allow-list of topics declared in
`app.shopify.webhooks.SUBSCRIBED_TOPICS`. This module exists to take that
declarative list and reconcile it against what's actually registered on
the shop side. Concretely:

  - `list_existing(...)` reads the shop's current subscriptions.
  - `reconcile(...)` computes a diff against `SUBSCRIBED_TOPICS` and creates
    anything missing (and optionally deletes anything stale).

Idempotency is the contract: re-running `reconcile` on a fully-registered
shop is a no-op. This is what lets `flask shopify register-webhooks` be
re-run safely after every deploy.

Topic name format: our internal allow-list uses REST-style slashes
(`orders/create`). The GraphQL `WebhookSubscriptionTopic` enum uses
SCREAMING_SNAKE (`ORDERS_CREATE`). `_to_graphql_topic` converts.

All mutations here are sent through `client.query(..., allow_mutation=True)`
because TR-46 blocks mutations by default. Webhook subscription management
is a legitimate authorized exception.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.shopify.client import ShopifyClient
from app.shopify.errors import ShopifyError
from app.shopify.webhooks import SUBSCRIBED_TOPICS


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookSubscription:
    id: str
    topic: str  # GraphQL form: 'ORDERS_CREATE'
    callback_url: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconcileResult:
    store_key: str
    callback_url: str
    created: tuple[str, ...]  # topics newly created
    already_present: tuple[str, ...]  # topics already wired to this callback URL
    relocated: tuple[str, ...]  # topics that existed but pointed elsewhere; recreated
    deleted: tuple[str, ...]  # topics removed because they were no longer in the allow-list


class WebhookAdminError(ShopifyError):
    """Raised when a webhook mutation surfaces userErrors or returns malformed data."""


_LIST_SUBSCRIPTIONS = """
query Subscriptions($cursor: String) {
  webhookSubscriptions(first: 250, after: $cursor) {
    edges {
      cursor
      node {
        id
        topic
        endpoint {
          __typename
          ... on WebhookHttpEndpoint { callbackUrl }
        }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""

_CREATE_SUBSCRIPTION = """
mutation Create($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) {
    webhookSubscription { id topic }
    userErrors { field message }
  }
}
"""

_DELETE_SUBSCRIPTION = """
mutation Delete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
"""


def _to_graphql_topic(topic: str) -> str:
    """`'orders/create'` → `'ORDERS_CREATE'`."""
    return topic.replace("/", "_").upper()


def _from_graphql_topic(graphql_topic: str) -> str:
    """`'ORDERS_CREATE'` → `'orders/create'`. Inverse of `_to_graphql_topic`."""
    return graphql_topic.replace("_", "/").lower()


def _callback_for(base_url: str, store_key: str) -> str:
    return f"{base_url.rstrip('/')}/webhooks/{store_key}"


def list_existing(client: ShopifyClient, store_key: str) -> list[WebhookSubscription]:
    """Return every webhook subscription currently registered for the shop."""
    out: list[WebhookSubscription] = []
    cursor: str | None = None
    while True:
        data = client.query(store_key, _LIST_SUBSCRIPTIONS, variables={"cursor": cursor})
        conn = data.get("webhookSubscriptions") or {}
        edges = conn.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            endpoint = node.get("endpoint") or {}
            # Non-HTTP endpoints (EventBridge, PubSub) report a different __typename
            # and have no callbackUrl — we treat their callback_url as "" so the
            # reconciler ignores them.
            callback_url = (
                str(endpoint.get("callbackUrl") or "")
                if endpoint.get("__typename") == "WebhookHttpEndpoint"
                else ""
            )
            out.append(
                WebhookSubscription(
                    id=str(node.get("id") or ""),
                    topic=str(node.get("topic") or ""),
                    callback_url=callback_url,
                )
            )
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = edges[-1].get("cursor") if edges else None
        if cursor is None:
            break
    return out


def create(
    client: ShopifyClient,
    store_key: str,
    *,
    topic: str,
    callback_url: str,
) -> str:
    """Create one subscription. Returns the new subscription id (gid)."""
    graphql_topic = _to_graphql_topic(topic)
    data = client.query(
        store_key,
        _CREATE_SUBSCRIPTION,
        variables={
            "topic": graphql_topic,
            "sub": {"callbackUrl": callback_url, "format": "JSON"},
        },
        allow_mutation=True,
    )
    body = data.get("webhookSubscriptionCreate") or {}
    errors = body.get("userErrors") or []
    if errors:
        raise WebhookAdminError(f"webhookSubscriptionCreate userErrors for {topic!r}: {errors}")
    sub = body.get("webhookSubscription") or {}
    new_id = sub.get("id")
    if not new_id:
        raise WebhookAdminError(
            f"webhookSubscriptionCreate returned no subscription id for {topic!r}: {body!r}"
        )
    return str(new_id)


def delete(client: ShopifyClient, store_key: str, *, subscription_id: str) -> None:
    """Delete one subscription by gid."""
    data = client.query(
        store_key,
        _DELETE_SUBSCRIPTION,
        variables={"id": subscription_id},
        allow_mutation=True,
    )
    body = data.get("webhookSubscriptionDelete") or {}
    errors = body.get("userErrors") or []
    if errors:
        raise WebhookAdminError(
            f"webhookSubscriptionDelete userErrors for {subscription_id!r}: {errors}"
        )


def _classify_topic(
    slash_topic: str,
    subs_for_topic: list[WebhookSubscription],
    *,
    callback_url: str,
    path_marker: str,
) -> tuple[str, list[WebhookSubscription]]:
    """Decide what to do with one allow-list topic.

    Returns (action, stale_subs) where action is one of:
      - "already_present": leave alone
      - "relocated": delete stale_subs (ours but at the wrong URL), then create
      - "create": no existing subscription owned by us; create
    """
    on_target = [s for s in subs_for_topic if s.callback_url == callback_url]
    if on_target:
        return "already_present", []
    ours_relocated = [s for s in subs_for_topic if path_marker in s.callback_url]
    if ours_relocated:
        return "relocated", ours_relocated
    return "create", []


def reconcile(
    client: ShopifyClient,
    store_key: str,
    *,
    base_url: str,
    prune_unknown: bool = False,
    dry_run: bool = False,
) -> ReconcileResult:
    """Make the shop's subscriptions match `SUBSCRIBED_TOPICS`.

    Behavior:
      - Topics in `SUBSCRIBED_TOPICS` not present on the shop → created.
      - Topics present but pointing at a different callback URL on this
        store key → deleted and recreated (`relocated`).
      - Topics present at the right URL → left alone (`already_present`).
      - Topics on the shop NOT in `SUBSCRIBED_TOPICS`, scoped to the same
        callback URL prefix → deleted only when `prune_unknown=True`.
        Subscriptions pointing at unrelated URLs (other apps, manually
        registered) are never touched.

    `dry_run=True` reports what would happen without calling create/delete.
    """
    callback_url = _callback_for(base_url, store_key)
    path_marker = f"/webhooks/{store_key}"
    existing = list_existing(client, store_key)

    by_topic: dict[str, list[WebhookSubscription]] = {}
    for sub in existing:
        by_topic.setdefault(sub.topic, []).append(sub)

    target_graphql_topics = {_to_graphql_topic(t) for t in SUBSCRIBED_TOPICS}

    created: list[str] = []
    already_present: list[str] = []
    relocated: list[str] = []

    for slash_topic in sorted(SUBSCRIBED_TOPICS):
        graphql_topic = _to_graphql_topic(slash_topic)
        action, stale = _classify_topic(
            slash_topic,
            by_topic.get(graphql_topic, []),
            callback_url=callback_url,
            path_marker=path_marker,
        )
        if action == "already_present":
            already_present.append(slash_topic)
            continue
        if action == "relocated":
            relocated.append(slash_topic)
            for s in stale:
                if not dry_run:
                    delete(client, store_key, subscription_id=s.id)
        else:
            created.append(slash_topic)
        if not dry_run:
            create(client, store_key, topic=slash_topic, callback_url=callback_url)

    deleted: list[str] = []
    if prune_unknown:
        for sub in existing:
            if sub.topic in target_graphql_topics or path_marker not in sub.callback_url:
                continue
            deleted.append(_from_graphql_topic(sub.topic))
            if not dry_run:
                delete(client, store_key, subscription_id=sub.id)

    return ReconcileResult(
        store_key=store_key,
        callback_url=callback_url,
        created=tuple(created),
        already_present=tuple(already_present),
        relocated=tuple(relocated),
        deleted=tuple(deleted),
    )
