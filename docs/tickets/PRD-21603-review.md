# PRD-21603 · Code review

Status: frozen @ d105e0e

## Round 1 · 2026-11-05 · reviewed d105e0e (base 65ecbbb)

Note on base: branch is `dev/PRD-21603`, reviewed commit is `d105e0e`, base is `65ecbbb` (merge-base with `origin/main`). `origin/main` @ `465815f` already contains PRD-21573's scaffold merge. `dev/PRD-21603`'s own merge commit `db931f0` ("Merge dev/PRD-21573") brought in the same scaffold independently (tree-identical to `origin/main` except for one unrelated file, `docs/tickets/PRD-21573-review.md`, and this ticket's own `docs/tickets/PRD-21603.*` files). So the reviewable diff for this ticket is `65ecbbb..d105e0e` (6 files), not a second review of PRD-21573's own scaffold code, per this ticket's own Handoff note.

Verdict: approved

### Findings

1. [suggestion] src/partner_center_mcp/tools/customers.py:590 — the upstream call passes the raw `offset` argument (possibly `None`) in the `params` dict, while `offset_used = offset or 0` is computed separately for the `has_more` arithmetic a few lines below. Harmless today (the shared `api_client.py` strips `None` params before the request, so `offset=None` and omitting the key are equivalent upstream), but passing `offset_used` in both places would keep the "effective offset" single-sourced instead of computed twice.

### Scope / standard checklist (MCP, mcp-track.md §4.3)

- Credentials: no new credential path; reuses the existing `X-PartnerCenter-Token` → contextvar flow unchanged. Tool does not log `filter` or any upstream payload.
- Contract: tool name/params/response match the tech design's Data & API contract section exactly (`filter`, `limit`, `offset` → `items`/`total_count`/`has_more`); errors use the shared `error_envelope`/`PartnerCenterError.to_envelope()`.
- Tests: 9 new cases (TC1–TC9) cover AC1, AC3–AC8 with mocked HTTP; AC2 (missing credential) is intentionally not re-tested — already covered by PRD-21573's shared `test_middleware.py`, documented in both the design's Test strategy and the Self-test section. Self-test documents a real regression catch (3 new tests were first named identically to existing `subscriptions` tests, silently shadowing them; caught by comparing collected-test count, renamed, re-ran — 33/33 pass) — this is exactly the kind of evidence R3 asks for, not a bare "passed" claim.
- README: `## Tools` table and `## Known Gaps` updated consistently; `## Scope` list updated. Connector section (`reference/readme-connector-section.md`) untouched, diff confirmed identical in Self-test.
- No Chinese characters in any file this ticket touches (Self-test grep, zero hits).
- No new dependencies; `pyproject.toml`/`uv.lock` unchanged.
- Cross-repo/field compatibility (mcp-track.md §1.2): not applicable — this ticket only adds a tool to an existing MCP server, no new header/field shared with another repo.
- REVIEW.md §2 BLOCKER list: none of the 12 conditions apply (no schema/migration, no `mspack`/forced/direct push yet, merge into `dev/PRD-21603` was clean per Handoff, no new dependency left undocumented, gates and `ac.json` all green with evidence).

No must-fix. Approved to merge.
