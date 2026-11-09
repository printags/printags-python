"""Client for the Printags REST API (https://docs.printags.com), version 2.

Every method maps to one operation of the OpenAPI document https://www.printags.com/openapi.json, under the
operation's name in snake_case (``listOrders`` is ``list_orders``).

    from printags import Printags

    printags = Printags(api_key=os.environ["PRINTAGS_API_KEY"])
    page = printags.list_orders("@me", per_page=10)
"""

from typing import Any, BinaryIO, Dict, Iterator, Optional, Tuple, Union
from urllib.parse import quote

import httpx

__version__ = "0.1.0"
__all__ = [
    "Printags",
    "PrintagsError",
    "BadRequestError",
    "AuthenticationError",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
]

PRODUCTION_URL = "https://api.printags.com"
SANDBOX_URL = "https://sandbox-api.printags.com"


class PrintagsError(Exception):
    """An error answer of the API: ``status`` is the HTTP status, ``code`` the machine-readable code when the API gave one."""

    def __init__(self, status: int, body: Any, retry_after: Optional[int] = None) -> None:
        # The API answers errors in two shapes: {code, message} and {error: {code, message}}.
        detail = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else body
        detail = detail if isinstance(detail, dict) else {}
        super().__init__(detail.get("message") or f"Printags API error {status}")
        self.status = status
        self.code: Optional[str] = detail.get("code")
        self.body = body
        #: Seconds to wait before retrying, when the API answered 429 with a Retry-After header.
        self.retry_after = retry_after


class BadRequestError(PrintagsError):
    """400: the request is invalid (``code`` says why, e.g. ``INVALID_QUANTITY``)."""


class AuthenticationError(PrintagsError):
    """401: missing or unknown API key."""


class PaymentRequiredError(PrintagsError):
    """402: the account has no plan (``SUBSCRIPTION_REQUIRED``)."""


class PermissionDeniedError(PrintagsError):
    """403: the key is revoked or targets another account."""


class NotFoundError(PrintagsError):
    """404: the account, order or file does not exist for this key."""


class RateLimitError(PrintagsError):
    """429: too many requests; wait ``retry_after`` seconds."""


class ServerError(PrintagsError):
    """5xx: an error on Printags' side."""


_ERRORS = {400: BadRequestError, 401: AuthenticationError, 402: PaymentRequiredError, 403: PermissionDeniedError,
           404: NotFoundError, 429: RateLimitError}


def _error(response: httpx.Response) -> PrintagsError:
    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text or None}
    retry_after = response.headers.get("retry-after")
    cls = ServerError if response.status_code >= 500 else _ERRORS.get(response.status_code, PrintagsError)
    return cls(response.status_code, body, int(retry_after) if retry_after and retry_after.isdigit() else None)


def _seg(segment: str) -> str:
    # "@" is valid in a path segment and is how the API writes the current account ("@me"): keep it as is.
    return quote(str(segment), safe="@")


class Printags:
    """Synchronous client. Use it as a context manager, or call ``close()``, to release its connections."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        sandbox: bool = False,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """
        :param api_key: ``sk_live_...`` for real orders, ``sk_test_...`` for test orders.
        :param sandbox: use https://sandbox-api.printags.com, which only accepts ``sk_test_`` keys.
        :param base_url: another server (overrides ``sandbox``).
        :param http_client: an ``httpx.Client`` of your own (proxies, retries, tests); its base URL is not used.
        """
        if sandbox and api_key and not api_key.startswith("sk_test_"):
            # The sandbox host would refuse it with a 403: refusing here keeps a real key from leaving the machine.
            raise ValueError("The sandbox only accepts test API keys (sk_test_...)")
        self.base_url = (base_url or (SANDBOX_URL if sandbox else PRODUCTION_URL)).rstrip("/")
        self._headers = {"Accept": "application/json", "User-Agent": user_agent or f"printags-python/{__version__}"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Printags":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _send(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
              headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> httpx.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        response = self._client.request(method, self.base_url + path, params=params,
                                        headers={**self._headers, **(headers or {})}, **kwargs)
        if response.is_error:
            raise _error(response)
        return response

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        return response.json() if response.content else None

    # --- Accounts

    def get_current_account(self) -> Dict[str, Any]:
        """The account the API key acts on."""
        return self._request("GET", "/v2/accounts/@me")

    def get_account(self, account_id: str) -> Dict[str, Any]:
        """``account_id``: an account id, or ``@me``."""
        return self._request("GET", f"/v2/accounts/{_seg(account_id)}")

    # --- Orders

    def list_orders(self, account_id: str, *, page: Optional[int] = None, per_page: Optional[int] = None,
                    status: Optional[str] = None, search: Optional[str] = None) -> Dict[str, Any]:
        """One page of the account's orders, newest first: ``{"orders": [...], "pagination": {...}}``.

        ``status`` is one status or several separated by commas (``"draft,in_queue"``).
        """
        return self._request("GET", f"/v2/accounts/{_seg(account_id)}/orders",
                             params={"page": page, "per_page": per_page, "status": status, "search": search})

    def iter_orders(self, account_id: str, *, per_page: int = 50, status: Optional[str] = None,
                    search: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """Every order matching the filters, newest first, fetching the pages as it goes."""
        page = 1
        while True:
            result = self.list_orders(account_id, page=page, per_page=per_page, status=status, search=search)
            orders = result.get("orders") or []
            yield from orders
            total = (result.get("pagination") or {}).get("total")
            if not orders or (total is not None and page * per_page >= total):
                return
            page += 1

    def find_orders(self, account_id: str, *, metadata: Optional[str] = None, reference: Optional[str] = None,
                    fields: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", f"/v2/accounts/{_seg(account_id)}/orders/find",
                             params={"metadata": metadata, "reference": reference, "fields": fields})

    def get_order(self, account_id: str, order_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v2/accounts/{_seg(account_id)}/orders/{_seg(order_id)}")

    def create_order(self, account_id: str, order: Dict[str, Any], *,
                     idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Creates an order (body: see the ``createOrder`` operation). ``{"isDraft": True, ...}`` creates a draft that
        prints and charges nothing until it is validated.

        ``idempotency_key`` (8 to 100 characters, a UUID for example): a retry with the same key returns the order
        already created instead of creating, and charging, a second one.
        """
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request("POST", f"/v2/accounts/{_seg(account_id)}/orders", json=order, headers=headers)

    def validate_order(self, account_id: str, order_id: str) -> Dict[str, Any]:
        """Validates a draft order: production starts and the balance is charged."""
        return self._request("POST", f"/v2/accounts/{_seg(account_id)}/orders/{_seg(order_id)}/validate")

    def simulate_order_status(self, account_id: str, order_id: str, status: str) -> Dict[str, Any]:
        """Test keys only: moves a test order to any status of the life cycle (see ``list_order_statuses``)."""
        return self._request("POST", f"/v2/accounts/{_seg(account_id)}/orders/{_seg(order_id)}/simulate",
                             json={"status": status})

    # --- Files

    def upload_file(self, account_id: str, file: Union[bytes, BinaryIO], filename: str = "file") -> Dict[str, Any]:
        """Uploads an artwork (image or PDF): ``{"success": True, "files": {"file": {"id": ...}}}``. The file id goes in
        an order item's ``printingVisuals``."""
        return self._request("POST", f"/v2/accounts/{_seg(account_id)}/files", files={"file": (filename, file)})

    def get_file(self, account_id: str, file_id: str) -> Tuple[bytes, Optional[str]]:
        """Downloads a file: ``(data, content_type)``."""
        response = self._send("GET", f"/v2/accounts/{_seg(account_id)}/files/{_seg(file_id)}")
        return response.content, response.headers.get("content-type")

    # --- Reference data

    def list_order_statuses(self) -> Dict[str, Any]:
        """Every status an order goes through. No API key needed."""
        return self._request("GET", "/v2/statuses")

    def list_shipping_carriers(self, to_country: str, *, to_postal_code: Optional[str] = None,
                               weight: Optional[float] = None) -> Dict[str, Any]:
        """The carriers that deliver to a country (ISO 2-letter code). No API key needed."""
        return self._request("GET", "/v2/shipping/carriers",
                             params={"to_country": to_country, "to_postal_code": to_postal_code, "weight": weight})

    def calculate_shipping_rates(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Shipping prices for a destination and a list of items (see ``calculateShippingRates``). No API key needed."""
        return self._request("POST", "/v2/shipping/rates", json=request)

    def list_products(self) -> Dict[str, Any]:
        """The catalogue of products (card models) your account can order."""
        return self._request("GET", "/v2/products")
