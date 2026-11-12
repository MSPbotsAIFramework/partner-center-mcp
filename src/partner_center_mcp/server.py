import contextvars
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import PartnerCenterClient
from .config import Settings

# Per-request token isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
_gateway_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "partner_center_gateway_token", default=None
)


def get_client_from_context(settings: Settings) -> PartnerCenterClient | None:
    """Resolve the active PartnerCenterClient for the current request context."""
    token = _gateway_token_var.get()
    if not token:
        return None
    return PartnerCenterClient(token, settings.partner_center_base_url)


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-PartnerCenter-Token from request headers and stores it in the
    contextvar. Returns 401 if the header is missing on /mcp requests.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.headers.get("x-partnercenter-token")
        if not token:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": "This server requires the X-PartnerCenter-Token header containing a valid Partner Center access token",
                    "required_headers": ["X-PartnerCenter-Token"],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_token_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_token_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all Partner Center tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disable it so the server works
    # correctly behind a reverse proxy or docker network.
    mcp = FastMCP(
        name="partner-center-mcp",
        instructions=(
            "Microsoft CSP / Partner Center is the API partners use to manage "
            "their customers' Microsoft cloud subscriptions (Microsoft 365, "
            "Azure, Dynamics) under the Cloud Solution Provider program. "
            "csp_list_customers lists every customer tenant under the "
            "partner (id, company name, domain, relationship to partner); "
            "csp_list_customer_subscriptions lists every subscription a given "
            "customer tenant has (offer name, quantity, status, billing cycle, "
            "commitment end date) — call it with the customer's Entra tenant "
            "id to see what they are currently licensed for. All tools act on "
            "the calling partner's own data via a per-request partner access "
            "token; there is no cross-partner access."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        json_response=True,
    )

    client_factory: Callable[[], PartnerCenterClient | None] = lambda: get_client_from_context(
        settings
    )

    from .tools import customers, subscriptions

    subscriptions.register(mcp, client_factory)
    customers.register(mcp, client_factory)

    return mcp
