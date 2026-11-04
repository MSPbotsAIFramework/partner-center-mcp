# partner-center-mcp

MCP server for **Microsoft CSP / Partner Center** — exposes Partner Center's
customer/subscription management API as MCP tools for partners managing
their customers' Microsoft cloud subscriptions under the Cloud Solution
Provider (CSP) program.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Overview

- Stateless HTTP service. No credentials are ever persisted — each request
  supplies its own credential via a header, used only for the lifetime of
  that single request.
- Supports concurrent requests; per-request credential isolation is done via
  Python `contextvars`, not a global/shared client instance.
- Entry points: `POST /mcp` (MCP protocol) and `GET /health` (health check).
- Default port: `8080` (configurable via `MCP_HTTP_PORT`).
- This service never performs the Partner Center App+User OAuth exchange —
  it expects an already-valid access token per request (see Authentication).

## Scope

First version: **1 read-only tool**, `csp_list_customer_subscriptions`. This
repo is shared by three sibling tickets that each add one tool for this same
connector (coordinated through this repo's `dev-lock`):

- `csp_list_customer_subscriptions` (this ticket, PRD-21573) — list a
  customer tenant's subscriptions.
- `csp_update_customer_subscription_quantity` (PRD-21574) — not yet
  implemented, see Known Gaps.
- `csp_list_customers` (PRD-21603) — not yet implemented, see Known Gaps.

## Authentication

Partner Center access is obtained via Microsoft's App+User OAuth flow
(Entra ID, one-time interactively-consented refresh token, non-standard
refresh grant against `login.microsoftonline.com`). This service does **not**
perform that exchange: it expects the gateway/connector layer to have
already obtained a valid access token for resource
`https://api.partnercenter.microsoft.com` and to forward only that token —
the same shape as this org's `ms-graph-mcp` connector. This service never
sees the Client ID, Client Secret, Tenant ID, or Refresh Token used to
obtain the token.

### HEADER Authentication Parameters

| Header | Type | Required | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-PartnerCenter-Token` | string | Yes | None | None | A bearer access token already issued for resource `https://api.partnercenter.microsoft.com` via the partner's Entra ID app (App+User auth), forwarded as-is as the upstream `Authorization: Bearer <token>` | `eyJhbGciOi...` |

Missing the header returns `401`:
```json
{
  "error": "Missing credentials",
  "message": "This server requires the X-PartnerCenter-Token header containing a valid Partner Center access token",
  "required_headers": ["X-PartnerCenter-Token"]
}
```

## Environment Variables

| Variable | Type | Required | Default | Description |
|---|---|---|---|---|
| `MCP_HTTP_PORT` | int | No | `8080` | HTTP listen port |
| `MCP_HTTP_HOST` | string | No | `0.0.0.0` | HTTP listen address |
| `PARTNER_CENTER_BASE_URL` | string | No | `https://api.partnercenter.microsoft.com/v1` | Partner Center API base URL. Overridable for a possible future sovereign-cloud endpoint; no confirmed MSPbots customer needs one today |

## MCP Endpoint

- `POST /mcp` — MCP protocol (streamable HTTP transport)
- `GET /health` — health check, returns `{"status": "ok"}`

## Tools

| Tool | Description | Params |
|---|---|---|
| `csp_list_customer_subscriptions` | List every Partner Center subscription a CSP customer tenant has (id, offer name, quantity, status, billing cycle, unit type, commitment end date). | customer_tenant_id, limit? |

## Typical Workflows

- **Check a customer's current licensing before a quote or renewal**: call
  `csp_list_customer_subscriptions` with the customer's Entra tenant id to
  see what they are currently subscribed to, at what quantity and billing
  cycle.

## Test Example

```bash
# Health check
curl -s http://localhost:8080/health

# Call the tool via the MCP protocol (streamable HTTP) — requires an
# initialize handshake first per the MCP spec; abbreviated example below
# shows the tool-call request body only:
curl -s -X POST http://localhost:8080/mcp \
  -H "X-PartnerCenter-Token: <access-token>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id-from-initialize>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "csp_list_customer_subscriptions",
      "arguments": {"customer_tenant_id": "11111111-2222-3333-4444-555555555555"}
    }
  }'
```

Expected: `200` with `{"items": [...], "total_count": N, "has_more": false}`
on a valid token and tenant; a structured `error_envelope` (`unauthorized` /
`not_found` / `invalid_argument` / `rate_limited` / `upstream_error`) on
failure — never a raw 500 or stack trace.

## API Reference

- REST reference (Swagger-generated): https://learn.microsoft.com/en-us/partner-center/developer/partner-center-rest-api-reference
- Authentication: https://learn.microsoft.com/en-us/partner-center/developer/partner-center-authentication

## Known Gaps

- **No real Partner Center partner account is available to this pipeline.**
  All tests use mocked HTTP responses. True end-to-end verification against
  a live account is needed before this is relied on for a real customer
  (needs a real vendor account — does not block delivery per SOP).
- **`csp_update_customer_subscription_quantity` (PRD-21574) and
  `csp_list_customers` (PRD-21603)** are not yet implemented — this repo
  currently exposes only `csp_list_customer_subscriptions`.
- **Partner Center's exact error-body shape** (`description` vs `message`
  vs `error`) is taken from the publicly documented reference, not verified
  against a live call. `api_client.py`'s `_raise_for_status` tries
  `message`, then `description`, then `error`, then falls back to the raw
  body — only this internal parsing would need adjusting if the real shape
  differs; the external MCP error envelope would not change.
- **Pagination**: Partner Center's subscriptions endpoint is not documented
  as paginated; `limit`/`has_more` is applied defensively against an
  unusually large customer, not because a confirmed `nextLink`/cursor
  mechanism exists for this endpoint.
