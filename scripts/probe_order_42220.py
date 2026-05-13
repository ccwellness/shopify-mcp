"""One-shot probe: pull discount details for order 7117040550127 from Shopify."""

import json

from dotenv import load_dotenv

load_dotenv()

from app.shopify.client import ShopifyClient  # noqa: E402
from app.shopify.config import load_store_configs  # noqa: E402

QUERY = """
query($id: ID!) {
  order(id: $id) {
    name
    tags
    note
    sourceName
    sourceIdentifier
    currentTotalDiscountsSet { shopMoney { amount currencyCode } }
    currentTotalPriceSet { shopMoney { amount currencyCode } }
    discountCode
    discountCodes
    discountApplications(first: 20) {
      edges {
        node {
          __typename
          allocationMethod
          targetSelection
          targetType
          value {
            __typename
            ... on MoneyV2 { amount currencyCode }
            ... on PricingPercentageValue { percentage }
          }
          ... on DiscountCodeApplication { code }
          ... on AutomaticDiscountApplication { title }
          ... on ManualDiscountApplication { title description }
          ... on ScriptDiscountApplication { title }
        }
      }
    }
    lineItems(first: 20) {
      edges {
        node {
          title
          sku
          quantity
          originalUnitPriceSet { shopMoney { amount } }
          discountedUnitPriceSet { shopMoney { amount } }
          totalDiscountSet { shopMoney { amount } }
          discountAllocations {
            allocatedAmountSet { shopMoney { amount } }
            discountApplication {
              __typename
              ... on DiscountCodeApplication { code }
              ... on ManualDiscountApplication { title description }
              ... on AutomaticDiscountApplication { title }
            }
          }
        }
      }
    }
  }
}
"""


def main() -> None:
    client = ShopifyClient(load_store_configs())
    data = client.query("lubelife", QUERY, {"id": "gid://shopify/Order/7117040550127"})
    print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
