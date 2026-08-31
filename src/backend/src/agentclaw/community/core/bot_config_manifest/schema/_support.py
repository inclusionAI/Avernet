"""Shared primitives every rule module needs: the collector and the checks.

Split out so ``validator`` (document shape) and ``entries`` (per-entry fields)
can share them without either importing the other — the two halves of one pass,
not a layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

from agentclaw.community.core.bot_config_manifest.capabilities import (
    KIND_SOURCE,
    ManifestCapabilities,
)
from agentclaw.community.core.bot_config_manifest.schema.placeholders import (
    unknown_placeholders,
)
from agentclaw.community.core.bot_config_manifest.schema.violations import Violation

#: ``sha256:`` + 64 lowercase hex. One form, because a digest that can be
#: written two ways is a digest two comparisons can disagree about.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Conservative on purpose: letters, digits, and the four separators a real
#: workspace path needs. Everything a shell, a URL or a filesystem would have to
#: quote is refused here rather than escaped at four downstream call sites.
_PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9._/\-${}]+$")

#: An identifier for a skill or a tool. It becomes a directory name and, for a
#: tool, a command name.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class Context:
    """The pass's accumulator.

    Rules append rather than raise: ``PUT`` is all-or-nothing and answers with
    the whole list, so a rule that raised would turn the answer into a queue of
    one problem at a time.
    """

    capabilities: ManifestCapabilities
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Names declared under top-level ``sources``, for ``from`` to resolve
    #: against. Populated before any entry is walked.
    source_names: set[str] = field(default_factory=set)
    #: Named sources actually referenced, so an unused one can be reported —
    #: schema §2.3 makes that a hint, not an error.
    referenced_sources: set[str] = field(default_factory=set)

    def add(self, location: str, code: str, message: str) -> None:
        """Record one violation against a document location."""
        self.violations.append(
            Violation(location=location, code=code, message=message)
        )

    def require_source_support(self, location: str, source_kind: str) -> bool:
        """Refuse a source form nothing can resolve yet. True when supported."""
        if self.capabilities.supports(KIND_SOURCE, source_kind):
            return True
        self.add(
            location,
            "unsupported_source",
            f"source form '{source_kind}' is not supported: "
            + self.capabilities.reason_for(KIND_SOURCE, source_kind),
        )
        return False


def check_placeholders(ctx: Context, location: str, value: Any) -> None:
    """Refuse any ``${...}`` name outside the whitelist.

    Applied to every string in the ``manifest`` section **except** inline
    ``content`` bodies and the ``script`` body. Those two are literal text a
    caller authored for something else to read — a knowledge file or a shell
    script legitimately contains ``${HOME}`` — and scanning them would refuse
    valid documents for a substitution that never applies to them (schema §4).
    """
    if not isinstance(value, str):
        return
    unknown = unknown_placeholders(value)
    if unknown:
        ctx.add(
            location,
            "unknown_placeholder",
            "unknown substitution variable(s) "
            + ", ".join(f"${{{name}}}" for name in unknown)
            + "; allowed: ${BOT_ID}, ${BOT_ENGINE_TYPE}, ${BOT_ENV}, "
            "${BOT_TENANT}, ${BOT_ARCH}",
        )


def check_https_url(ctx: Context, location: str, value: Any) -> None:
    """Refuse a source URL that is not an absolute https URL, or carries a token.

    **Userinfo is the rule with teeth.** ``https://user:token@host/path`` puts a
    secret in a document that is stored as written, read back verbatim by
    ``GET``, and recorded as provenance by the platform's own materialisation
    (W11). Those three are precisely what the encrypted, never-readable
    credential store (schema §2.1) exists to avoid, so an inline credential is
    refused rather than accepted-and-redacted — redaction cannot un-store what
    was already accepted somewhere else.

    https-only for the same reason the credential store's ``allowed_prefixes``
    are: a fetched skill or identity file is content the bot will run on, and
    plaintext transport puts an intermediary in charge of it.
    """
    if not isinstance(value, str):
        ctx.add(location, "invalid_source", "source URL must be a string")
        return
    try:
        parts = urlsplit(value)
    except ValueError:
        ctx.add(location, "invalid_source", "source URL is not a valid URL")
        return
    if parts.scheme != "https" or not parts.netloc:
        ctx.add(
            location,
            "invalid_source",
            "source URL must be an absolute https:// URL",
        )
        return
    if "@" in parts.netloc:
        ctx.add(
            location,
            "source_url_has_userinfo",
            "source URL must not embed credentials "
            "(https://user:token@host/...); declare a named credential and "
            "reference it with `auth` instead",
        )


def check_digest(ctx: Context, location: str, value: Any) -> None:
    """Refuse a digest that is not ``sha256:<64 hex>``."""
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        ctx.add(
            location,
            "invalid_digest",
            "digest must be 'sha256:' followed by 64 lowercase hex characters",
        )


def check_relative_path(
    ctx: Context, location: str, value: Any, *, what: str
) -> bool:
    """Refuse an absolute path, a ``..`` segment, or exotic characters.

    Returns True when the value is usable by later rules (nesting, basenames).

    ``..`` is checked **per segment**, not as a substring: a directory named
    ``..config`` is legitimate and contains the two characters. A substring test
    would refuse it while still admitting nothing more.
    """
    if not isinstance(value, str) or not value:
        ctx.add(location, "invalid_path", f"{what} must be a non-empty string")
        return False
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        ctx.add(location, "absolute_path", f"{what} must be workspace-relative, not absolute")
        return False
    if value.startswith("~"):
        ctx.add(location, "absolute_path", f"{what} must not start with '~'")
        return False
    segments = value.split("/")
    if any(segment == ".." for segment in segments):
        ctx.add(
            location,
            "path_traversal",
            f"{what} must not contain a '..' segment",
        )
        return False
    if not _PATH_CHARS_RE.match(value):
        ctx.add(
            location,
            "invalid_path",
            f"{what} may only contain letters, digits, '.', '_', '-', '/' and "
            "${...} placeholders",
        )
        return False
    # Placeholders inside the value are *not* checked here. Every string this
    # function sees is also swept by the walker that scans a whole entry (or a
    # whole named source), and checking in both places reported one typo as two
    # problems — which reads as two things to fix.
    return True


def check_name(ctx: Context, location: str, value: Any, *, what: str) -> bool:
    """Refuse a name that is not a bare identifier.

    A skill or tool name carries **no position** (schema §2.0): where it lands is
    the engine's decision. A name containing ``/`` is a caller trying to choose a
    path through the one field that promises not to be one.
    """
    if not isinstance(value, str) or not _NAME_RE.match(value):
        ctx.add(
            location,
            "invalid_name",
            f"{what} must start with a letter or digit and contain only "
            "letters, digits, '.', '_' and '-'",
        )
        return False
    return True


def duplicates(values: Iterable[str]) -> list[str]:
    """The values appearing more than once, in first-appearance order."""
    seen: dict[str, int] = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    return [value for value, count in seen.items() if count > 1]
