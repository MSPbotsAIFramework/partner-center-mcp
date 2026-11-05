"""csp_list_customers (PRD-21603).

The upstream `size`/`offset` query parameter names and semantics are taken
from the ticket's own description, not verified against a live Partner
Center account (no test account available at design time — see
docs/tickets/PRD-21603.md Open questions). If they turn out to mean
something different, only the params dict built below needs to change —
this tool's own `limit`/`offset` contract is unaffected either way, because
the mapped response is additionally truncated to `effective_limit`
client-side regardless of what `size` actually does upstream.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import PartnerCenterClient, PartnerCenterError
from ._common import NO_TOKEN, clamp_limit


def _map_customer(raw: dict) -> dict:
    profile = raw.get("companyProfile") or {}
    return {
        "id": raw.get("id"),
        "company_name": profile.get("companyName"),
        "domain": profile.get("domain"),
        "relationship_to_partner": raw.get("relationshipToPartner"),
    }


def register(mcp: FastMCP, client_factory: Callable[[], PartnerCenterClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def csp_list_customers(
        filter: Annotated[
            str | None,
            Field(
                description="Raw Partner Center $filter expression (e.g. \"companyProfile/companyName eq 'Contoso'\"). Forwarded verbatim; not parsed or validated here."
            ),
        ] = None,
        limit: Annotated[
            int | None,
            Field(
                description="Max customer tenants to return. Defaults to 50; capped at 200 regardless of the value passed. Must not be negative."
            ),
        ] = None,
        offset: Annotated[
            int | None,
            Field(
                description="Zero-based record offset for pagination. Defaults to 0. Must not be negative."
            ),
        ] = None,
    ) -> str:
        """List every customer tenant under the caller's CSP partner relationship.

        Returns id, company_name, domain, and relationship_to_partner for
        each customer tenant the partner identified by the request's
        credential manages. Supports an optional raw $filter passthrough
        and limit/offset pagination (default page size 50, hard cap 200).
        """
        if limit is not None and limit < 0:
            return error_envelope("invalid_argument", "limit must not be negative", False)
        if offset is not None and offset < 0:
            return error_envelope("invalid_argument", "offset must not be negative", False)

        client = client_factory()
        if client is None:
            return NO_TOKEN

        effective_limit = clamp_limit(limit)
        offset_used = offset or 0
        try:
            result = await client.get(
                "/customers",
                params={"$filter": filter, "size": effective_limit, "offset": offset},
            )
        except PartnerCenterError as e:
            return e.to_envelope()

        raw_items = (result or {}).get("items") or []
        total_count = (result or {}).get("totalCount")
        if total_count is None:
            total_count = len(raw_items)

        returned = [_map_customer(item) for item in raw_items[:effective_limit]]
        has_more = (offset_used + len(returned)) < total_count

        return dump_json_capped(
            {"items": returned, "total_count": total_count, "has_more": has_more}
        )
