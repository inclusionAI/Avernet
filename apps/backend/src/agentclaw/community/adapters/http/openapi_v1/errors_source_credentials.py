"""Error→(status, fixed message) rows for the source-credentials surface (W3).

Merged into ``responses.ENVELOPE_ERRORS`` — the single map sits at the
architecture line cap, so a group that needs rows brings them (the
skill-center/space-skill precedents, and W1's config-manifest rows).

Fixed messages, never raw exception text: the one rule this surface adds
is that a secret's *value* never rides an error (the credential name may;
the service family is written to keep it that way).
"""
from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
    CredentialNotFoundError,
    CredentialNotOwnedError,
    MasterKeyUnavailableError,
)

SOURCE_CREDENTIALS_ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    # ── Order is load-bearing: lookup is the first isinstance match, and
    # every row below but the last subclasses CredentialError — the base
    # must come after its subclasses. ──
    # Named miss in the caller's tenant — masked like every 404 here.
    CredentialNotFoundError: (404, "Not found"),
    # The one mutation boundary: rotation and delete are the owning
    # application's alone. 403, not 404 — the caller can legitimately
    # name the credential (reads are tenant-wide), it just cannot
    # change it.
    CredentialNotOwnedError: (
        403,
        "Source credential is owned by another application",
    ),
    # Fail-closed production posture: no master key, no plaintext at rest.
    # 503, not 422: the input is fine; the platform's secret store is not
    # resolvable *right now* — an operator fixes the key store and retries.
    MasterKeyUnavailableError: (503, "Source credential storage is unavailable"),
    # Input validation (bad name/header/prefixes, reserved type) — the
    # family base, deliberately last among this module's rows.
    CredentialError: (422, "Source credential input is invalid"),
}
