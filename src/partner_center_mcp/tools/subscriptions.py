from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import PartnerCenterClient, PartnerCenterError
from ._common import NO_TOKEN, clamp_limit

# Partner Center's subscription field names -> this tool's response field names
# (per the ticket's cited response shape: items[].{id, offerName, quantity,
# status, billingCycle, unitType, commitmentEndDate}).
_FIELD_MAP = {
    "id": "id",
    "offerName": "offer_name",
    "quantity": "quantity",
    "status": "status",
    "billingCycle": "billing_cycle",
    "unitType": "unit_type",
    "commitmentEndDate": "commitment_end_date",
}


def _map_subscription(raw: dict) -> dict:
    return {dst: raw.get(src) for src, dst in _FIELD_MAP.items()}


def register(mcp: FastMCP, client_factory: Callable[[], PartnerCenterClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def csp_list_customer_subscriptions(
        customer_tenant_id: Annotated[
            str,
            Field(
                description="The customer's Entra (Azure AD) tenant id (e.g. Rewst's ORG.MAPPING.ms_tenant_id)."
            ),
        ],
        limit: Annotated[
            int | None,
            Field(
                description="Max subscriptions to return. Defaults to 50; capped at 200 regardless of the value passed."
            ),
        ] = None,
    ) -> str:
        """List every Partner Center subscription a CSP customer tenant has.

        Returns id, offer_name, quantity, status, billing_cycle, unit_type,
        and commitment_end_date for each subscription the customer tenant
        currently has with this partner.
        """
        if not customer_tenant_id or not customer_tenant_id.strip():
            return error_envelope(
                "invalid_argument", "customer_tenant_id is required and must not be blank", False
            )

        client = client_factory()
        if client is None:
            return NO_TOKEN

        effective_limit = clamp_limit(limit)
        try:
            result = await client.get(f"/customers/{customer_tenant_id}/subscriptions")
        except PartnerCenterError as e:
            return e.to_envelope()

        raw_items = (result or {}).get("items") or []
        total_count = (result or {}).get("totalCount")
        if total_count is None:
            total_count = len(raw_items)

        returned = [_map_subscription(item) for item in raw_items[:effective_limit]]
        has_more = total_count > len(returned)

        return dump_json_capped(
            {"items": returned, "total_count": total_count, "has_more": has_more}
        )
