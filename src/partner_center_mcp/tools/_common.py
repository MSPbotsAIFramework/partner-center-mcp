from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No Partner Center credentials. Send the X-PartnerCenter-Token header.",
    False,
)

DEFAULT_LIMIT = 50
HARD_CAP_LIMIT = 200


def clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT, hard_cap: int = HARD_CAP_LIMIT) -> int:
    """Resolve the effective page size: default when unset, always capped —
    never rejected for being "too high", just clamped. Shared by every
    list-shaped tool this repo will ever add (PRD-21574/PRD-21603)."""
    if limit is None:
        return default
    if limit <= 0:
        return default
    return min(limit, hard_cap)
