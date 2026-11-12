# PRD-21573 · Code review

Status: frozen @ ba25a89

## Round 1 · 2026-11-05 · reviewed dev/PRD-21573 @ ba25a89 (base main @ 331c669)

Verdict: approved

### Scope

`git diff 331c669..ba25a89` (full first-version scaffold + `csp_list_customer_subscriptions`): `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `README.md`, `src/partner_center_mcp/**`, `tests/**`, `uv.lock`. (The two follow-up doc commits, `f038e81` and `65ecbbb`, touch only `docs/tickets/PRD-21573.md` and are not re-reviewed as code.)

### Verification performed

- `uv sync` + `uv run pytest -v` on the synced worktree (base `main` unchanged since branch cut, nothing to merge): 24 passed, 0 failed — matches the Self-test section in `docs/tickets/PRD-21573.md`.
- `docs/tickets/PRD-21573.ac.json` validated with `ticket_store.py validate`: 9 cases, ledger OK.
- README `## MSPbots Connector` section diffed byte-for-byte against `reference/readme-connector-section.md`: identical.
- `grep -rnP '[\x{4e00}-\x{9fff}]'` across all code/docs/config files added or edited by this dev leg (excluding the ticket doc's own quoted Chinese source evidence, which predates this dev leg and is not code): zero hits.
- Credential model (`X-PartnerCenter-Token`, single header, no server-side OAuth exchange, `contextvars`-based per-request isolation, reset in `finally`) matches `ms-graph-mcp`'s accepted precedent; `_STATUS_TO_CODE`, retry/backoff policy, and `_json.py` match `connectsecure-mcp`'s accepted precedent line for line.
- Tool contract (name, params, return shape, error envelope) matches the tech design's Data & API contract exactly; AC1–AC8 each have a passing, non-trivial test (mocked HTTP, concurrency test exercises real `asyncio.gather` interleaving, retry-exhaustion test exercises the real backoff loop via `httpx.MockTransport`).
- SOP §14 delivery checklist: contract section (stateless, header-only credential, 401 on missing header, DNS-rebinding protection disabled, lifespan propagated to the outer app, `tools/call` exercised live in Self-test) and container section (multi-stage build, non-root user, `curl` installed, `EXPOSE 8080` + `HEALTHCHECK`, lockfile committed, connector README section present before the credential table) both check out against the diff.

### Findings

None. No must-fix, no suggestions worth recording — this is a first-version scaffold copied faithfully from two already-accepted precedent repos (`ms-graph-mcp`, `connectsecure-mcp`), the one new tool's logic is small and fully covered by tests that actually exercise the claimed behavior (verified by re-running the suite, not by trusting the Self-test section alone).
