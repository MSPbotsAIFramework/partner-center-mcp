"""Gateway credential middleware tests: missing-header 401, header values
correctly reaching the per-request contextvar, and no leakage across
concurrent requests (no global/shared state)."""

import asyncio

import httpx
import pytest
from starlette.testclient import TestClient

from partner_center_mcp.__main__ import _build_http_app
from partner_center_mcp.config import Settings
from partner_center_mcp.server import (
    GatewayTokenMiddleware,
    _gateway_token_var,
    create_mcp_server,
    get_client_from_context,
)


def _make_app():
    settings = Settings()
    mcp = create_mcp_server(settings)
    return _build_http_app(mcp, settings), settings


def test_health_is_local_and_does_not_require_credentials():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_missing_token_header_returns_401():
    """TC2/AC2: no X-PartnerCenter-Token header -> 401 listing the header as
    required, and (since the request never reaches the handler) no upstream
    call is possible."""
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["required_headers"] == ["X-PartnerCenter-Token"]


def test_header_present_reaches_request_context():
    # Directly exercises the middleware's contextvar plumbing without a full
    # MCP protocol round-trip: confirms the header value that arrives on the
    # request is exactly what get_client_from_context sees, and that it's
    # reset afterward (no leakage to the next request).
    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["token"] = _gateway_token_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"x-partnercenter-token", b"test-token-123")],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["token"] == "test-token-123"
    # After the request completes, the contextvar must be reset — a fresh
    # get() outside any request context sees no leftover credential.
    assert _gateway_token_var.get() is None


def test_client_factory_returns_none_without_context():
    settings = Settings()
    assert get_client_from_context(settings) is None


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_leak_token_or_tenant(monkeypatch):
    """TC8/AC7: two concurrent calls with different tokens and
    customer_tenant_id values must each reach the upstream with only their
    own call's token and tenant — never the other's."""
    from partner_center_mcp import api_client

    seen: list[tuple[str, str]] = []  # (Authorization header, tenant from URL path)

    async def handler(request: httpx.Request) -> httpx.Response:
        # Yield control so the two concurrent tasks actually interleave
        # instead of each running to completion before the other starts —
        # that's the only way this test could catch token/tenant leakage.
        await asyncio.sleep(0.01)
        auth = request.headers.get("authorization", "")
        # Settings.partner_center_base_url is ".../v1", so the path is
        # /v1/customers/{tenant}/subscriptions.
        tenant = request.url.path.split("/")[3]
        seen.append((auth, tenant))
        return httpx.Response(200, json={"totalCount": 0, "items": []})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api_client, "_http_client", mock_client)

    settings = Settings()
    mcp = create_mcp_server(settings)

    async def one_call(token: str, tenant: str):
        # Mirrors what GatewayTokenMiddleware does per-request: set the
        # contextvar before the handler runs, reset it after. asyncio.gather
        # runs each coroutine as its own Task, and contextvars are copied
        # per-Task, so this reproduces real concurrent-request isolation.
        ctx = _gateway_token_var.set(token)
        try:
            await mcp.call_tool("csp_list_customer_subscriptions", {"customer_tenant_id": tenant})
        finally:
            _gateway_token_var.reset(ctx)

    await asyncio.gather(
        one_call("token-A", "tenant-aaa"),
        one_call("token-B", "tenant-bbb"),
    )

    assert len(seen) == 2
    pairs = dict(seen)
    assert pairs["Bearer token-A"] == "tenant-aaa"
    assert pairs["Bearer token-B"] == "tenant-bbb"
