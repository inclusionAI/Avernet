"""Limits and deployment allowlists for the guarded fetcher (W2, #1470).

Single source for every number the transport enforces — schema §5 of
``docs/bot-config-manifest/manifest-schema.zh-CN.md``. Fetch-time limits
(remote content sizes) live here and nowhere else: the schema module refuses
at PUT only what it can see at write time, and duplicating fetch numbers
there is how the two drift.

The deployment transport allowlist is *config-driven, not env-driven* — it
turns a deployment decision (corporate HTTP mirror, an internal content
proxy) into an exception through ``application.yaml``'s
``user_config.bot_config_manifest`` block, per the repo rule that raw
environment access belongs to config loading and composition roots, never
to core. Core here stays pure: ``transport_allowlist_from_config`` parses
the merged YAML tree, and the composition root hands the result to
``GuardedFetcher`` as a constructor value. It widens nothing else — a host
on the allowlist still goes through address validation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

#: Address resolution seam: host → resolved IP string list. Real DNS via
#: socket in production; tests inject deterministic answers (including the
#: check-public/connect-private rebinding pair).
Resolver = Callable[[str], list[str]]

#: ``schema §5`` — per-entry fetch caps, in bytes, by category.
FETCH_ENTRY_LIMITS: Mapping[str, int] = {
    "skills": 100 * 1024 * 1024,
    "resources_file": 100 * 1024 * 1024,
    "identity": 1 * 1024 * 1024,
    "cli_tools": 200 * 1024 * 1024,
    "resources_archive": 200 * 1024 * 1024,
    "resources_unpacked": 500 * 1024 * 1024,
}

#: ``schema §5`` — one archive may not explode beyond this many members.
ARCHIVE_MEMBER_LIMIT = 5000

#: ``schema §5`` — totals for a single apply run, LEDGERED by W5's
#: ``apply/budget.ApplyFetchBudget`` (carried on ``ApplyContext``,
#: consulted before each entry's fetch and charged after): an apply that
#: outruns these ends in bounded time rather than holding the per-bot apply
#: lock past the TTL the stale-lock reaper trusts.
APPLY_FETCH_TOTAL_LIMIT = 500 * 1024 * 1024
APPLY_BUDGET_S = 300.0

#: Per network operation (each hop, each read). A multi-hop entry is
#: therefore NOT bounded to one of these — its wall clock is bounded by the
#: apply-level budget W4 threads; that is the schema §5 "单条 60s"的诚实形态。
FETCH_TIMEOUT_S = 60.0

#: Redirect hop budget — deep chains are a redirected-SSF pattern, so the
#: cap is both a robustness and a safety bound.
MAX_REDIRECTS = 5

#: Schemes accepted without a deployment exception (W2 验收: 仅 https)。
SAFE_SCHEMES = frozenset({"https"})

#: The digest vocabulary W2 fixed and every later wave shares: ``sha256:``
#: followed by exactly 64 lowercase hex. The fetcher mints these addresses
#: (declared-digest validation, FetchedObject.sha256) and the W11 content
#: store uses them as its addressing scheme — one regex, one vocabulary, no
#: per-module copies that drift.
#:
#: ``\A``/``\Z`` rather than ``^``/``$``: in Python ``$`` also matches
#: immediately before a trailing newline, so a digest with a ``\n`` tail
#: would pass and then fail downstream as a mismatch or a missing address —
#: the wrong taxonomy for malformed config. A fixed-width vocabulary has no
#: room for a newline.
DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


#: Where the allowlist lives in the YAML tree, as one constant: the block
#: name under ``user_config`` and the key inside it.
_MANIFEST_BLOCK = "bot_config_manifest"
_TRANSPORT_ALLOWLIST_KEY = "fetch_transport_allowlist"


def transport_allowlist_from_config(
    settings: Mapping[str, Any],
) -> frozenset[str]:
    """Hosts exempted from public-only resolution and, where needed, the
    https-only rule, from the merged ``user_config`` tree
    (``AppConfig.user_config``) of ``application.yaml``.

    Matching is exact-host — the DNS-rebinding lesson: a hostname on the
    list is the hostname exempted. Allowlisted hosts keep every other rule:
    redirect budget, byte caps, hop-by-hop re-validation. The list admits
    *destinations*, not behaviors.

    The block is optional and empty by default (no deployment exception);
    a present-but-malformed block is a configuration error and raises
    ``ValueError`` — a typo in yaml must fail its reader loudly, not
    silently fetch strictly. Consumers: the composition root that
    constructs ``GuardedFetcher`` reads ``user_config`` through this seam
    (kept in core as a pure parser over the already-loaded tree, so core
    never touches raw environment itself).
    """
    block = settings.get(_MANIFEST_BLOCK)
    if block is None:
        return frozenset()
    if not isinstance(block, Mapping):
        raise ValueError(
            f"user_config.{_MANIFEST_BLOCK} must be a mapping, "
            f"got {type(block).__name__}"
        )
    hosts = block.get(_TRANSPORT_ALLOWLIST_KEY, [])
    if hosts is None:
        hosts = []
    if isinstance(hosts, str) or not isinstance(hosts, (list, tuple)):
        raise ValueError(
            f"user_config.{_MANIFEST_BLOCK}.{_TRANSPORT_ALLOWLIST_KEY} "
            f"must be a list of hostnames, got {type(hosts).__name__}"
        )
    for host in hosts:
        if not isinstance(host, str):
            raise ValueError(
                f"user_config.{_MANIFEST_BLOCK}.{_TRANSPORT_ALLOWLIST_KEY} "
                f"must contain hostnames only, got {type(host).__name__}"
            )
    return frozenset(h.strip() for h in hosts if h.strip())


@dataclass(frozen=True)
class FetchBudget:
    """Transport budget for ONE request, as the guarded fetcher issues it.

    Per-entry caps and the per-hop timeout — the vocabulary a
    ``FetchRequest`` names. The *apply-scope* ledger (shared wall clock and
    byte total) is a different object at a different layer:
    ``apply/budget.ApplyFetchBudget``, consulted per entry by the entry
    fetch pipeline (W5). The two share this module's numbers and nothing
    else.
    """

    category: str = "resources_file"

    @property
    def entry_limit(self) -> int:
        default = FETCH_ENTRY_LIMITS["resources_file"]
        return FETCH_ENTRY_LIMITS.get(self.category, default)

    @property
    def timeout_s(self) -> float:
        return FETCH_TIMEOUT_S
