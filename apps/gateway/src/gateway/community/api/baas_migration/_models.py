"""Service API contract for migrating a secbaas API key onto the gateway.

Transport-agnostic by construction (Rule 7): the core migrator returns a
:class:`MigrationResult`, and the web adapter maps :class:`MigrationOutcome`
onto an HTTP status. Neither side knows the other — the adapter never imports
``community.core`` (the layer rules forbid it), and the core never names a
status code.

Every refusal is a value rather than an exception, deliberately. A migration
can fail for reasons the caller is expected to *act* on — pick a different app
name, clean up a policy — so the reason has to survive the trip to the response
body intact. Exceptions would either be caught and flattened at the seam
(losing the structured detail) or forced into the adapter's imports (breaking
the layering). The one thing that still raises is a fault nobody can act on: a
database that is down is not an outcome.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class MigrationOutcome(StrEnum):
    """Why a migration ended the way it did.

    String-valued so the adapter's status table can be written and tested
    against the same names the core produces.
    """

    MIGRATED = "migrated"
    """The credential and every grant it implied were written."""

    KEY_NOT_FOUND = "key_not_found"
    """No ACTIVE ``baas_api_key`` row matches the presented key.

    Covers both "no such prefix" and "prefix found, hash did not verify". The
    two are deliberately indistinguishable: telling them apart would turn this
    endpoint into an oracle for which prefixes exist.
    """

    ALREADY_MIGRATED = "already_migrated"
    """This exact key already has a row in ``avernet_application``.

    Established by hash equality on the row holding the prefix, not by
    assumption — a prefix collision with an unrelated app reports
    :attr:`PREFIX_CONFLICT` instead.
    """

    PREFIX_CONFLICT = "prefix_conflict"
    """The key's 8-character prefix is taken by a *different* application.

    Vanishingly unlikely (62^8), and unrecoverable from the caller's side: the
    prefix is a property of a key we cannot regenerate without invalidating it.
    """

    APP_NAME_TAKEN = "app_name_taken"
    """``(app_name, env)`` is already claimed. The caller retries with another name."""

    WILDCARD_POLICY = "wildcard_policy"
    """The source policy is ``allowed_bots: ["*"]`` — allow-all.

    Refused rather than flattened. ``ac_bot_app_grant`` records one row per
    (bot, delegating user); "every bot, including ones not created yet" has no
    representation there, and materialising today's bots would silently freeze a
    permission that was open-ended.
    """

    INVALID_GRANT_TARGETS = "invalid_grant_targets"
    """One or more bot references are not ``{bot_id}:{entity_id}``.

    Refused whole rather than migrated minus the bad entries: an app that looks
    migrated while reaching fewer bots than before is the failure this refusal
    exists to prevent. The offending values come back so they can be fixed at
    the source.
    """

    UNSUPPORTED_APP_TYPE = "unsupported_app_type"
    """``app_type`` is neither ``app`` nor ``bot``, so no grants can be derived."""

    VALUE_TOO_LONG = "value_too_long"
    """A copied value does not fit the destination column.

    Refused, never truncated. A truncated ``user_id`` or ``bot_id`` produces a
    grant row that no lookup can ever match — an authorization that looks live
    in every listing and answers "no" on every request.
    """


@dataclass(frozen=True)
class MigratedGrant:
    """One ``ac_bot_app_grant`` row the migration wrote."""

    bot_id: str
    user_id: str
    owner_id: str
    env: str


@dataclass(frozen=True)
class MigratedApp:
    """The ``avernet_application`` row the migration wrote, and its grants.

    Carries no key material. The plaintext key is the caller's already — they
    presented it — and the row stores only its hash, so there is nothing to
    return and nothing worth logging.
    """

    id: int
    app_name: str
    app_type: str
    owners: str
    tenant: str
    env: str
    api_key_prefix: str
    source_key_id: int
    grants: tuple[MigratedGrant, ...]


@dataclass(frozen=True)
class MigrationResult:
    """What a migration attempt produced — one value for success and refusal alike."""

    outcome: MigrationOutcome
    """Which case this attempt landed in. The only field always populated."""

    app: MigratedApp | None = None
    """The rows that were written, or ``None`` when none were.

    **``None`` for every outcome except :attr:`MigrationOutcome.MIGRATED`.**
    There is no third state, and no partial one: a refusal writes nothing at all,
    because the application row, its grants and its log rows are a single
    transaction. So this being ``None`` always means the caller's secbaas key is
    still their only working credential.

    The refusals that produce it, by where they are decided:

    * before any lookup — ``value_too_long`` for an over-long ``app_name``;
    * at the credential check — ``key_not_found`` (no ACTIVE source row matched,
      or one matched by prefix and its hash did not verify);
    * while deriving grants from the source row — ``wildcard_policy``,
      ``invalid_grant_targets``, ``unsupported_app_type``, and
      ``value_too_long`` for a value no grant column can hold;
    * at the insert, when a unique key refuses it — ``already_migrated``,
      ``prefix_conflict``, ``app_name_taken``.

    A database that is down is **not** among them: that raises rather than
    returning, because it is not something the caller can act on.

    Prefer :attr:`succeeded` over testing this for ``None``. The outcome is the
    single source of truth for whether anything was written; reading it off this
    field instead makes two facts that can drift apart.
    """

    message: str = ""
    """Human-readable reason for a refusal. Empty on success."""

    detail: Mapping[str, object] = field(default_factory=dict)
    """Structured specifics of a refusal, as plain data for a response body.

    Whatever the caller needs in order to act — the taken name and its
    environment, the offending bot references, the field that overflowed. Empty
    on success, and empty for a refusal that needs no specifics beyond its
    outcome.
    """

    @property
    def succeeded(self) -> bool:
        """Whether anything was written. True exactly when ``app`` is not ``None``."""
        return self.outcome is MigrationOutcome.MIGRATED
