import json

import httpx
import pytest

from printags import (AuthenticationError, NotFoundError, Printags, PrintagsError, RateLimitError, ServerError)


def make(handler, **kwargs):
    """A client whose requests go to `handler` instead of the network."""
    return Printags(http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_sends_bearer_key_to_production():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "acc_1"})

    assert make(handler, api_key="sk_live_abc").get_current_account() == {"id": "acc_1"}
    assert seen == {"url": "https://api.printags.com/v2/accounts/@me", "auth": "Bearer sk_live_abc"}


def test_sandbox_refuses_live_key_before_any_request():
    with pytest.raises(ValueError):
        Printags(api_key="sk_live_abc", sandbox=True)
    assert Printags(api_key="sk_test_abc", sandbox=True).base_url == "https://sandbox-api.printags.com"


def test_path_segments_are_encoded():
    def handler(request):
        assert request.url.raw_path == b"/v2/accounts/@me/orders/a%2Fb"
        return httpx.Response(200, json={})

    make(handler).get_order("@me", "a/b")


@pytest.mark.parametrize("status, body, cls, code", [
    (401, {"code": "UNAUTHORIZED", "message": "Missing key"}, AuthenticationError, "UNAUTHORIZED"),
    (404, {"error": {"code": "ORDER_NOT_FOUND", "message": "No such order"}}, NotFoundError, "ORDER_NOT_FOUND"),
    (502, None, ServerError, None),
])
def test_errors_are_typed(status, body, cls, code):
    client = make(lambda r: httpx.Response(status, json=body) if body else httpx.Response(status, text="Bad gateway"))
    with pytest.raises(cls) as info:
        client.get_order("@me", "o1")
    assert isinstance(info.value, PrintagsError)
    assert info.value.status == status
    assert info.value.code == code


def test_rate_limit_gives_retry_after():
    client = make(lambda r: httpx.Response(429, headers={"Retry-After": "12"}, json={"message": "Slow down"}))
    with pytest.raises(RateLimitError) as info:
        client.list_products()
    assert info.value.retry_after == 12
    assert str(info.value) == "Slow down"


def test_iter_orders_walks_every_page():
    pages = {"1": ["a", "b"], "2": ["c", "d"], "3": ["e"]}
    calls = []

    def handler(request):
        page = request.url.params["page"]
        calls.append((page, request.url.params["per_page"], request.url.params.get("status")))
        orders = [{"id": i} for i in pages[page]]
        return httpx.Response(200, json={"orders": orders, "pagination": {"total": 5, "page": int(page), "per_page": 2}})

    ids = [o["id"] for o in make(handler).iter_orders("@me", per_page=2, status="draft")]
    assert ids == ["a", "b", "c", "d", "e"]
    assert calls == [("1", "2", "draft"), ("2", "2", "draft"), ("3", "2", "draft")]


def test_list_orders_drops_empty_filters():
    def handler(request):
        assert dict(request.url.params) == {"per_page": "10"}
        return httpx.Response(200, json={"orders": [], "pagination": {"total": 0}})

    make(handler).list_orders("@me", per_page=10, search="")


def test_create_draft_order_with_idempotency_key():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("idempotency-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "orderId": "o1"})

    order = {"isDraft": True, "reference": "Order-1", "shipTo": {"name": "Jean Dupont"},
             "items": [{"productId": "ntag213_pvccard_white", "quantity": 10}]}
    result = make(handler, api_key="sk_test_abc").create_order("@me", order, idempotency_key="order-1-attempt")
    assert result == {"success": True, "orderId": "o1"}
    assert seen == {"method": "POST", "path": "/v2/accounts/@me/orders", "key": "order-1-attempt", "body": order}


def test_create_order_without_key_sends_no_header():
    def handler(request):
        assert "idempotency-key" not in request.headers
        return httpx.Response(200, json={"success": True, "orderId": "o2"})

    make(handler).create_order("@me", {"isDraft": True, "shipTo": {}})


@pytest.mark.parametrize("entry, message", [
    ({"success": False, "error": {"code": "UPLOAD_FAILED", "message": "Storage unavailable"}}, "Storage unavailable"),
    ({"success": False, "error": {}}, "Printags API error 200"),  # a JS Error object serializes to {}
])
def test_refused_upload_raises(entry, message):
    client = make(lambda r: httpx.Response(200, json={"success": True, "files": {"file": entry}}))
    with pytest.raises(PrintagsError) as info:
        client.upload_file("@me", b"PNG", "front.png")
    assert str(info.value) == message
    assert info.value.status == 200


def test_success_false_anywhere_raises():
    client = make(lambda r: httpx.Response(200, json={"success": False, "error": "Order not validated"}))
    with pytest.raises(PrintagsError, match="Order not validated"):
        client.validate_order("@me", "o1")


def test_upload_and_download_file():
    def handler(request):
        if request.method == "POST":
            assert b'name="file"; filename="front.png"' in request.content
            return httpx.Response(200, json={"success": True, "files": {"file": {"id": "f1"}}})
        return httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"})

    client = make(handler)
    assert client.upload_file("@me", b"PNG", "front.png")["files"]["file"]["id"] == "f1"
    assert client.get_file("@me", "f1") == (b"PNG", "image/png")
