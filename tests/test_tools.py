"""tools/list snapshot + csp_list_customer_subscriptions/csp_list_customers
behavior tests.

No real network calls: the client seen by the tool is either a fake
stand-in (records calls / returns or raises a fixed result) for the tool's
own mapping/validation logic, or the real PartnerCenterClient routed through
an httpx.MockTransport for the retry-exhaustion test, which exercises
api_client's actual backoff loop.
"""

import asyncio
import json

import httpx
import pytest

from partner_center_mcp.api_client import PartnerCenterClient, PartnerCenterError
from partner_center_mcp.config import Settings
from partner_center_mcp.server import create_mcp_server

EXPECTED_TOOLS = {
    "csp_list_customer_subscriptions": {"required": {"customer_tenant_id"}, "read_only": True},
    "csp_list_customers": {"required": set(), "read_only": True},
    "csp_update_customer_subscription_quantity": {
        "required": {"customer_tenant_id", "subscription_id", "quantity"},
        "read_only": False,
    },
}


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"

    by_name = {t.name: t for t in tools}
    for name, expected in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected["required"], f"{name}: required={required}"
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is expected["read_only"], f"{name}: readOnlyHint"
        assert len(tool.description or "") <= 500, f"{name}: description too long"
        first_line = (tool.description or "").strip().splitlines()[0]
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.parametrize(
    "status_code,expected_code,expected_retryable",
    [
        (0, "upstream_error", True),
        (400, "invalid_argument", False),
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_error_envelope_mapping(status_code, expected_code, expected_retryable):
    err = PartnerCenterError(status_code, "boom")
    envelope = json.loads(err.to_envelope())
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["retryable"] is expected_retryable
    assert envelope["error"]["message"] == "boom"


class _FakeClient:
    """Minimal PartnerCenterClient stand-in: returns a fixed result, or
    raises a fixed PartnerCenterError, and records every call made.

    `result`/`error` drive `get` for the list-shaped (get-only) tools.
    `get_result`/`get_error` and `patch_result`/`patch_error` drive the two
    calls independently for PRD-21574's read-modify-write tool."""

    def __init__(
        self,
        result: dict | None = None,
        error: PartnerCenterError | None = None,
        get_result: dict | None = None,
        get_error: PartnerCenterError | None = None,
        patch_result: dict | None = None,
        patch_error: PartnerCenterError | None = None,
    ):
        self.calls: list[tuple[str, dict]] = []
        self.patch_calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else get_result
        self._error = error if error is not None else get_error
        self._patch_result = patch_result
        self._patch_error = patch_error

    async def get(self, path: str, params: dict | None = None):
        self.calls.append((path, params or {}))
        if self._error is not None:
            raise self._error
        return self._result

    async def patch(self, path: str, body: dict):
        self.patch_calls.append((path, body))
        if self._patch_error is not None:
            raise self._patch_error
        return self._patch_result


def _register(
    result: dict | None = None,
    error: PartnerCenterError | None = None,
    get_result: dict | None = None,
    get_error: PartnerCenterError | None = None,
    patch_result: dict | None = None,
    patch_error: PartnerCenterError | None = None,
):
    from mcp.server.fastmcp import FastMCP

    from partner_center_mcp.tools import subscriptions

    mcp = FastMCP(name="test")
    client = _FakeClient(
        result=result,
        error=error,
        get_result=get_result,
        get_error=get_error,
        patch_result=patch_result,
        patch_error=patch_error,
    )
    subscriptions.register(mcp, lambda: client)
    return mcp, client


async def _call(mcp, args: dict, tool: str = "csp_list_customer_subscriptions") -> dict:
    result = await mcp.call_tool(tool, args)
    return json.loads(result[0][0].text)


_ONE_SUBSCRIPTION = {
    "totalCount": 1,
    "items": [
        {
            "id": "abc123",
            "offerName": "Microsoft 365 Business Premium",
            "quantity": 25,
            "status": "active",
            "billingCycle": "monthly",
            "unitType": "Licenses",
            "commitmentEndDate": "2027-03-01T00:00:00Z",
        }
    ],
}


@pytest.mark.asyncio
async def test_list_subscriptions_maps_all_fields():
    """TC1/AC1: happy path — all 7 fields map from the upstream camelCase
    shape to this tool's snake_case response shape."""
    mcp, client = _register(result=_ONE_SUBSCRIPTION)
    payload = await _call(mcp, {"customer_tenant_id": "tenant-1"})

    assert payload["total_count"] == 1
    assert payload["has_more"] is False
    assert payload["items"] == [
        {
            "id": "abc123",
            "offer_name": "Microsoft 365 Business Premium",
            "quantity": 25,
            "status": "active",
            "billing_cycle": "monthly",
            "unit_type": "Licenses",
            "commitment_end_date": "2027-03-01T00:00:00Z",
        }
    ]
    assert client.calls == [("/customers/tenant-1/subscriptions", {})]


@pytest.mark.asyncio
async def test_blank_customer_tenant_id_is_rejected_before_any_call():
    """TC3/AC3: blank customer_tenant_id -> invalid_argument, zero upstream calls."""
    mcp, client = _register(result=_ONE_SUBSCRIPTION)
    payload = await _call(mcp, {"customer_tenant_id": "   "})

    assert payload["error"]["code"] == "invalid_argument"
    assert client.calls == []


@pytest.mark.asyncio
async def test_upstream_404_maps_to_not_found():
    """TC4/AC4: mocked upstream 404 -> not_found, carrying the upstream message."""
    mcp, _ = _register(error=PartnerCenterError(404, "Customer tenant not found"))
    payload = await _call(mcp, {"customer_tenant_id": "unknown-tenant"})

    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["message"] == "Customer tenant not found"
    assert payload["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_upstream_403_maps_to_unauthorized():
    """TC5/AC4: mocked upstream 403 -> unauthorized, carrying the upstream message."""
    mcp, _ = _register(error=PartnerCenterError(403, "No reseller relationship with this tenant"))
    payload = await _call(mcp, {"customer_tenant_id": "other-partners-tenant"})

    assert payload["error"]["code"] == "unauthorized"
    assert payload["error"]["message"] == "No reseller relationship with this tenant"


@pytest.mark.asyncio
async def test_zero_subscriptions_is_a_success_with_empty_items():
    """TC6/AC5: zero subscriptions is success, not an error."""
    mcp, _ = _register(result={"totalCount": 0, "items": []})
    payload = await _call(mcp, {"customer_tenant_id": "tenant-with-nothing"})

    assert "error" not in payload
    assert payload == {"items": [], "total_count": 0, "has_more": False}


def _subscriptions_page(n: int) -> dict:
    return {
        "totalCount": n,
        "items": [
            {
                "id": f"sub-{i}",
                "offerName": "Offer",
                "quantity": 1,
                "status": "active",
                "billingCycle": "monthly",
                "unitType": "Licenses",
                "commitmentEndDate": None,
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_default_limit_truncates_to_50_and_sets_has_more():
    """TC7/AC6: 75 items / totalCount 75, no limit passed -> exactly 50
    returned, has_more true; limit=200 -> all 75 returned, has_more false
    (boundary: under-cap vs at-cap)."""
    mcp, _ = _register(result=_subscriptions_page(75))
    payload = await _call(mcp, {"customer_tenant_id": "big-tenant"})
    assert len(payload["items"]) == 50
    assert payload["total_count"] == 75
    assert payload["has_more"] is True

    mcp, _ = _register(result=_subscriptions_page(75))
    payload = await _call(mcp, {"customer_tenant_id": "big-tenant", "limit": 200})
    assert len(payload["items"]) == 75
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_persistent_503_maps_to_retryable_upstream_error():
    """TC9/AC8: the upstream error that survives api_client's retries comes
    back as a structured, retryable upstream_error — not a raised exception
    or a silent empty success."""
    mcp, _ = _register(error=PartnerCenterError(503, "Service temporarily unavailable"))
    payload = await _call(mcp, {"customer_tenant_id": "tenant-1"})

    assert payload["error"]["code"] == "upstream_error"
    assert payload["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_api_client_retries_503_three_times_then_raises(monkeypatch):
    """Bonus (not in PRD-21573.ac.json, source: dev): exercises api_client's
    actual retry loop end to end — 503 on the initial attempt plus all 3
    retries (4 requests total), then PartnerCenterError(503) is raised so the
    tool layer's mapping (tested above) has something real to map."""
    from partner_center_mcp import api_client

    attempts = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        return httpx.Response(503, json={"message": "Service temporarily unavailable"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api_client, "_http_client", mock_client)
    # Skip the real backoff delay so this test doesn't sleep ~7s for nothing.
    monkeypatch.setattr(api_client, "_MAX_BACKOFF_SECONDS", 0.0)

    client = PartnerCenterClient("tok", "https://api.partnercenter.microsoft.com/v1")
    with pytest.raises(PartnerCenterError) as exc_info:
        await client.get("/customers/tenant-1/subscriptions")

    assert exc_info.value.status_code == 503
    assert len(attempts) == 4  # 1 initial attempt + 3 retries


# --- csp_update_customer_subscription_quantity (PRD-21574) ----------------

_TOOL = "csp_update_customer_subscription_quantity"
_BASE_ARGS = {"customer_tenant_id": "tenant-1", "subscription_id": "abc123", "quantity": 30}


@pytest.mark.asyncio
async def test_update_quantity_happy_path_gets_then_patches():
    """TC1/AC1: GET returns the current subscription (quantity 10); PATCH is
    called with the same object except quantity 30; the tool returns
    {id, quantity, status} from the PATCH response."""
    get_result = {"id": "abc123", "offerId": "offer-1", "quantity": 10, "status": "active"}
    patch_result = {"id": "abc123", "offerId": "offer-1", "quantity": 30, "status": "active"}
    mcp, client = _register(get_result=get_result, patch_result=patch_result)

    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert payload == {"id": "abc123", "quantity": 30, "status": "active"}
    assert client.calls == [("/customers/tenant-1/subscriptions/abc123", {})]
    assert client.patch_calls == [
        (
            "/customers/tenant-1/subscriptions/abc123",
            {"id": "abc123", "offerId": "offer-1", "quantity": 30, "status": "active"},
        )
    ]


@pytest.mark.asyncio
async def test_missing_required_argument_is_rejected_before_any_call():
    """TC3/AC3: customer_tenant_id, subscription_id, or quantity omitted
    entirely -> the tool call is rejected before the fake client's get/patch
    is ever reached (fastmcp's own required-argument check, same as every
    other tool on this server); blank string args -> this tool's own
    invalid_argument envelope, also before any upstream call."""
    from mcp.server.fastmcp.exceptions import ToolError

    for omit in ("customer_tenant_id", "subscription_id", "quantity"):
        args = {k: v for k, v in _BASE_ARGS.items() if k != omit}
        mcp, client = _register(get_result={}, patch_result={})
        with pytest.raises(ToolError):
            await mcp.call_tool(_TOOL, args)
        assert client.calls == []
        assert client.patch_calls == []

    for blank_field in ("customer_tenant_id", "subscription_id"):
        mcp, client = _register(get_result={}, patch_result={})
        payload = await _call(mcp, {**_BASE_ARGS, blank_field: "   "}, tool=_TOOL)
        assert payload["error"]["code"] == "invalid_argument"
        assert client.calls == []
        assert client.patch_calls == []


@pytest.mark.asyncio
async def test_invalid_quantity_is_rejected_before_any_call():
    """TC4/AC4: quantity negative, or not a whole number -> invalid_argument,
    zero upstream calls. quantity 0 is allowed through (Decisions: no
    client-side lower bound above 0)."""
    for bad_quantity in (-1, 2.5):
        mcp, client = _register(get_result={}, patch_result={})
        payload = await _call(mcp, {**_BASE_ARGS, "quantity": bad_quantity}, tool=_TOOL)
        assert payload["error"]["code"] == "invalid_argument"
        assert client.calls == []
        assert client.patch_calls == []


@pytest.mark.asyncio
async def test_get_404_maps_to_not_found_and_skips_patch():
    """TC5/AC5: mocked GET returns 404 -> not_found, PATCH never called."""
    mcp, client = _register(get_error=PartnerCenterError(404, "Subscription not found"))
    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["message"] == "Subscription not found"
    assert client.patch_calls == []


@pytest.mark.asyncio
async def test_get_403_maps_to_unauthorized():
    """TC6/AC6 (GET side): mocked GET returns 403 -> unauthorized, PATCH
    never called."""
    mcp, client = _register(get_error=PartnerCenterError(403, "No reseller relationship"))
    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert payload["error"]["code"] == "unauthorized"
    assert payload["error"]["message"] == "No reseller relationship"
    assert client.patch_calls == []


@pytest.mark.asyncio
async def test_patch_403_maps_to_unauthorized_after_successful_get():
    """TC6/AC6 (PATCH side): GET succeeds, mocked PATCH returns 403 ->
    unauthorized, carrying the upstream message."""
    mcp, client = _register(
        get_result={"id": "abc123", "quantity": 10, "status": "active"},
        patch_error=PartnerCenterError(403, "Token lacks write scope"),
    )
    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert payload["error"]["code"] == "unauthorized"
    assert payload["error"]["message"] == "Token lacks write scope"
    assert len(client.calls) == 1
    assert len(client.patch_calls) == 1


@pytest.mark.asyncio
async def test_patch_rejected_after_successful_get_surfaces_structured_error():
    """TC7/AC7: GET succeeds, mocked PATCH returns 409 (business-rule
    rejection) -> a structured, non-retryable error; the response never
    reports success even though the GET itself succeeded."""
    mcp, client = _register(
        get_result={"id": "abc123", "quantity": 10, "status": "active"},
        patch_error=PartnerCenterError(409, "Quantity below offer's minimum commitment"),
    )
    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert "quantity" not in payload
    assert "status" not in payload
    assert payload["error"]["retryable"] is False
    assert len(client.calls) == 1
    assert len(client.patch_calls) == 1


@pytest.mark.asyncio
async def test_persistent_get_503_maps_to_retryable_upstream_error():
    """TC8/AC8 (GET side): mocked GET returns 503 -> upstream_error,
    retryable true; PATCH never called."""
    mcp, client = _register(get_error=PartnerCenterError(503, "Service temporarily unavailable"))
    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert payload["error"]["code"] == "upstream_error"
    assert payload["error"]["retryable"] is True
    assert client.patch_calls == []


@pytest.mark.asyncio
async def test_persistent_patch_503_maps_to_retryable_upstream_error():
    """TC8/AC8 (PATCH side): GET succeeds, mocked PATCH returns 503 ->
    upstream_error, retryable true."""
    mcp, client = _register(
        get_result={"id": "abc123", "quantity": 10, "status": "active"},
        patch_error=PartnerCenterError(503, "Service temporarily unavailable"),
    )
    payload = await _call(mcp, _BASE_ARGS, tool=_TOOL)

    assert payload["error"]["code"] == "upstream_error"
    assert payload["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_concurrent_calls_do_not_leak_token_tenant_or_subscription(monkeypatch):
    """TC9/AC9: two concurrent calls, each with its own token, tenant id,
    and subscription id, must each have its own GET and PATCH reach the
    upstream with only that call's own values — never the other's. Mirrors
    test_middleware.py's concurrency test but exercises this tool's own
    GET-then-PATCH sequence end to end (real PartnerCenterClient + the
    GatewayTokenMiddleware contextvar, not the FakeClient)."""
    from partner_center_mcp import api_client
    from partner_center_mcp.server import _gateway_token_var

    seen: list[tuple[str, str, str, str]] = []  # (method, auth, tenant, subscription_id)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        auth = request.headers.get("authorization", "")
        # Settings.partner_center_base_url is ".../v1", so the path is
        # /v1/customers/{tenant}/subscriptions/{subscription_id}.
        parts = request.url.path.split("/")
        tenant, subscription_id = parts[3], parts[5]
        seen.append((request.method, auth, tenant, subscription_id))
        if request.method == "GET":
            return httpx.Response(
                200, json={"id": subscription_id, "quantity": 1, "status": "active"}
            )
        body = json.loads(request.content)
        return httpx.Response(200, json=body)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api_client, "_http_client", mock_client)

    settings = Settings()
    mcp = create_mcp_server(settings)

    async def one_call(token: str, tenant: str, subscription_id: str, quantity: int):
        ctx = _gateway_token_var.set(token)
        try:
            await mcp.call_tool(
                _TOOL,
                {
                    "customer_tenant_id": tenant,
                    "subscription_id": subscription_id,
                    "quantity": quantity,
                },
            )
        finally:
            _gateway_token_var.reset(ctx)

    await asyncio.gather(
        one_call("token-A", "tenant-aaa", "sub-aaa", 10),
        one_call("token-B", "tenant-bbb", "sub-bbb", 20),
    )

    assert len(seen) == 4  # 2 calls x (GET + PATCH)
    by_auth: dict[str, set[tuple[str, str]]] = {}
    for method, auth, tenant, subscription_id in seen:
        by_auth.setdefault(auth, set()).add((tenant, subscription_id))

    assert by_auth == {
        "Bearer token-A": {("tenant-aaa", "sub-aaa")},
        "Bearer token-B": {("tenant-bbb", "sub-bbb")},
    }


# --- csp_list_customers (PRD-21603) ---------------------------------------


def _register_customers(result: dict | None = None, error: PartnerCenterError | None = None):
    from mcp.server.fastmcp import FastMCP

    from partner_center_mcp.tools import customers

    mcp = FastMCP(name="test")
    client = _FakeClient(result=result, error=error)
    customers.register(mcp, lambda: client)
    return mcp, client


async def _call_customers(mcp, args: dict) -> dict:
    result = await mcp.call_tool("csp_list_customers", args)
    return json.loads(result[0][0].text)


_ONE_CUSTOMER = {
    "totalCount": 1,
    "items": [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "companyProfile": {
                "companyName": "EMD Networking Services",
                "domain": "emdnetworking.onmicrosoft.com",
            },
            "relationshipToPartner": "none",
        }
    ],
}


@pytest.mark.asyncio
async def test_list_customers_maps_all_fields():
    """TC1/AC1: happy path — the four surfaced fields map from the upstream
    shape (flat id/relationshipToPartner + nested companyProfile.*) to this
    tool's flat snake_case response shape."""
    mcp, client = _register_customers(result=_ONE_CUSTOMER)
    payload = await _call_customers(mcp, {})

    assert payload["total_count"] == 1
    assert payload["has_more"] is False
    assert payload["items"] == [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "company_name": "EMD Networking Services",
            "domain": "emdnetworking.onmicrosoft.com",
            "relationship_to_partner": "none",
        }
    ]
    assert client.calls == [("/customers", {"$filter": None, "size": 50, "offset": None})]


@pytest.mark.asyncio
async def test_negative_limit_is_rejected_before_any_call():
    """TC2/AC3: limit=-1 -> invalid_argument, zero upstream calls."""
    mcp, client = _register_customers(result=_ONE_CUSTOMER)
    payload = await _call_customers(mcp, {"limit": -1})

    assert payload["error"]["code"] == "invalid_argument"
    assert client.calls == []


@pytest.mark.asyncio
async def test_negative_offset_is_rejected_before_any_call():
    """TC3/AC3: offset=-1 -> invalid_argument, zero upstream calls."""
    mcp, client = _register_customers(result=_ONE_CUSTOMER)
    payload = await _call_customers(mcp, {"offset": -1})

    assert payload["error"]["code"] == "invalid_argument"
    assert client.calls == []


@pytest.mark.asyncio
async def test_filter_argument_is_forwarded_verbatim():
    """TC4/AC4: the filter argument reaches the upstream call as $filter,
    unmodified — no parsing or validation on this service's side."""
    mcp, client = _register_customers(result=_ONE_CUSTOMER)
    filter_expr = "companyProfile/companyName eq 'X'"

    await _call_customers(mcp, {"filter": filter_expr})

    assert client.calls == [
        ("/customers", {"$filter": filter_expr, "size": 50, "offset": None})
    ]


@pytest.mark.asyncio
async def test_customers_upstream_403_maps_to_unauthorized():
    """TC5/AC5: mocked upstream 403 (no active CSP relationship / invalid
    token) -> unauthorized, carrying the upstream message, not a raw 500."""
    mcp, _ = _register_customers(error=PartnerCenterError(403, "No active CSP relationship"))
    payload = await _call_customers(mcp, {})

    assert payload["error"]["code"] == "unauthorized"
    assert payload["error"]["message"] == "No active CSP relationship"


@pytest.mark.asyncio
async def test_zero_customers_is_a_success_with_empty_items():
    """TC6/AC6: zero customer tenants is success, not an error."""
    mcp, _ = _register_customers(result={"totalCount": 0, "items": []})
    payload = await _call_customers(mcp, {})

    assert "error" not in payload
    assert payload == {"items": [], "total_count": 0, "has_more": False}


def _customers_page(n: int, start: int = 0, total: int = 75) -> dict:
    return {
        "totalCount": total,
        "items": [
            {
                "id": f"cust-{start + i}",
                "companyProfile": {
                    "companyName": f"Customer {start + i}",
                    "domain": f"c{start + i}.onmicrosoft.com",
                },
                "relationshipToPartner": "none",
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_customers_default_limit_truncates_to_50_and_sets_has_more():
    """TC7/AC7: 75 items / totalCount 75, no limit/offset passed -> exactly
    50 returned (default page size), has_more true."""
    mcp, _ = _register_customers(result=_customers_page(75))
    payload = await _call_customers(mcp, {})

    assert len(payload["items"]) == 50
    assert payload["total_count"] == 75
    assert payload["has_more"] is True


@pytest.mark.asyncio
async def test_offset_advances_page_and_has_more_accounts_for_it():
    """TC8/AC7: offset=50 against a 75-total collection returns the
    remaining 25 and has_more accounts for (offset + returned_count) ==
    total, i.e. the page boundary: has_more is false exactly at the end."""
    mcp, client = _register_customers(result=_customers_page(25, start=50, total=75))
    payload = await _call_customers(mcp, {"offset": 50})

    assert len(payload["items"]) == 25
    assert payload["has_more"] is False  # 50 + 25 == 75, nothing left
    assert client.calls == [("/customers", {"$filter": None, "size": 50, "offset": 50})]


@pytest.mark.asyncio
async def test_customers_persistent_503_maps_to_retryable_upstream_error():
    """TC9/AC8: the upstream error that survives api_client's retries comes
    back as a structured, retryable upstream_error — not a raised exception,
    a raw 500, or a silently-empty success."""
    mcp, _ = _register_customers(error=PartnerCenterError(503, "Service temporarily unavailable"))
    payload = await _call_customers(mcp, {})

    assert payload["error"]["code"] == "upstream_error"
    assert payload["error"]["retryable"] is True
