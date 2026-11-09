# printags

Python SDK for the [Printags API](https://docs.printags.com): price, order and track printed PVC, NFC and wooden cards, shipped white-label.

Python 3.9 or later. One dependency: [httpx](https://www.python-httpx.org).

```sh
pip install printags
```

## Usage

```python
import os
import uuid

from printags import Printags

with Printags(api_key=os.environ["PRINTAGS_API_KEY"]) as printags:
    account = printags.get_current_account()

    # Upload the artwork, then cite its id in printingVisuals: [front, back].
    with open("front.png", "rb") as f:
        upload = printags.upload_file("@me", f, "front.png")

    # A draft prints and charges nothing until it is validated (in the dashboard or with validate_order).
    # The idempotency key makes a retry safe: the same key returns the order already created
    # instead of creating, and charging, a second one.
    order = printags.create_order(
        "@me",
        {
            "isDraft": True,
            "reference": "Order-2026-001",
            "shipTo": {
                "name": "Jean Dupont",
                "phone": "+33612345678",
                "email": "jean@example.com",
                "address": {
                    "addressLine1": "10 rue de la Paix",
                    "city": "Paris",
                    "postcode": "75002",
                    "countryCode": "FR",
                },
            },
            "items": [
                {
                    "productId": "ntag213_pvccard_white",
                    "quantity": 50,
                    "printingVisuals": [upload["files"]["file"]["id"]],
                    "nfc": {"type": "uri", "content": "https://www.printags.com"},
                }
            ],
        },
        idempotency_key=str(uuid.uuid4()),
    )
    print(printags.get_order("@me", order["orderId"]))

    # Every order, page after page.
    for o in printags.iter_orders("@me", status="draft"):
        print(o["id"], o["status"])
```

The exact fields of each body are in the [OpenAPI document](https://www.printags.com/openapi.json) and the [documentation](https://docs.printags.com).

Each method is one operation of the OpenAPI document, under its name in snake_case: `get_current_account`, `get_account`, `list_orders`, `find_orders`, `get_order`, `create_order`, `validate_order`, `simulate_order_status`, `upload_file`, `get_file`, `list_order_statuses`, `list_shipping_carriers`, `calculate_shipping_rates`, `list_products`. `iter_orders` walks the pages of `list_orders` for you.

## Errors

Every error answer raises `PrintagsError` (with `status`, `code` and `body`), or one of its subclasses: `BadRequestError` (400), `AuthenticationError` (401), `PaymentRequiredError` (402), `PermissionDeniedError` (403), `NotFoundError` (404), `RateLimitError` (429, with `retry_after` in seconds) and `ServerError` (5xx). An answer `{"success": false}` raises `PrintagsError` too, even with an HTTP 200: a file the API refused to store never comes back as if it were stored.

```python
from printags import NotFoundError

try:
    printags.get_order("@me", "unknown")
except NotFoundError as e:
    print(e.code)  # ORDER_NOT_FOUND
```

## Sandbox

A test key (`sk_test_...`) creates test orders that are neither charged nor manufactured. `sandbox=True` targets `https://sandbox-api.printags.com`, which only accepts test keys: a real key is refused before any request leaves your machine.

```python
printags = Printags(api_key=os.environ["PRINTAGS_TEST_KEY"], sandbox=True)
printags.simulate_order_status("@me", order_id, "shipped")
```

## License

[MIT](https://github.com/printags/printags-python/blob/main/LICENSE), © Printags SAS.

## Links

- Documentation: https://docs.printags.com
- OpenAPI document: https://www.printags.com/openapi.json
- MCP server and agent skills: https://github.com/printags/agent-skills
- JavaScript SDK and CLI: https://www.npmjs.com/package/printags
- Website: https://www.printags.com
