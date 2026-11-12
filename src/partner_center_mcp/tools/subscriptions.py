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


def _invalid_quantity(quantity: object) -> bool:
    """quantity must be a non-negative whole number (PRD-21574 AC4). No
    upper bound: Partner Center's own commitment rules are authoritative."""
    if isinstance(quantity, bool):
        return True
    if isinstance(quantity, int):
        return quantity < 0
    if isinstance(quantity, float):
        return not quantity.is_integer() or quantity < 0
    return True


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

    # PRD-21574: read-modify-write — Partner Center's documented PATCH
    # contract for this endpoint expects the full resource body, not a
    # sparse patch (see docs/tickets/PRD-21574.md Tech design, Option A).
    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=True
        )
    )
    async def csp_update_customer_subscription_quantity(
        customer_tenant_id: Annotated[
            str,
            Field(
                description="The customer's Entra (Azure AD) tenant id (e.g. Rewst's ORG.MAPPING.ms_tenant_id)."
            ),
        ],
        subscription_id: Annotated[
            str,
            Field(description="Partner Center subscription id (e.g. from csp_list_customer_subscriptions's id field)."),
        ],
        quantity: Annotated[
            int | float,
            Field(description="New license quantity for the subscription. Non-negative whole number; no upper bound."),
        ],
    ) -> str:
        """Change the license quantity on an existing Partner Center subscription.

        Fetches the subscription, sets its quantity to the requested value,
        and writes the full resource back (Partner Center's PATCH contract
        for this endpoint requires the complete object, not a sparse patch).
        Returns the updated id, quantity, and status.
        """
        if not customer_tenant_id or not customer_tenant_id.strip():
            return error_envelope(
                "invalid_argument", "customer_tenant_id is required and must not be blank", False
            )
        if not subscription_id or not subscription_id.strip():
            return error_envelope(
                "invalid_argument", "subscription_id is required and must not be blank", False
            )
        if _invalid_quantity(quantity):
            return error_envelope(
                "invalid_argument", "quantity must be a non-negative integer", False
            )

        client = client_factory()
        if client is None:
            return NO_TOKEN

        path = f"/customers/{customer_tenant_id}/subscriptions/{subscription_id}"
        try:
            current = await client.get(path)
        except PartnerCenterError as e:
            return e.to_envelope()

        updated_body = dict(current or {})
        updated_body["quantity"] = int(quantity)

        try:
            result = await client.patch(path, updated_body)
        except PartnerCenterError as e:
            return e.to_envelope()

        result = result or {}
        return dump_json_capped(
            {"id": result.get("id"), "quantity": result.get("quantity"), "status": result.get("status")}
        )
