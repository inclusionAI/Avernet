"""One apply's named-source state (W7, #1475).

The four things a single apply needs and nothing more: the document's
``sources`` declarations, the strict-mode baselines read back from the last
apply's report, a checkout cache keyed on the substituted ``(url, ref)``, and
the `SourceResolution` records the report will carry. It hangs on
``ApplyContext`` beside ``budget`` — mutable by design inside a frozen
context, the precedent that ruling cites — because the fetcher is a DI
singleton (state there would leak across applies) and a re-resolution per
entry would break "the same {git, ref} is pulled once per apply".
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitCheckout,
    GitSourceClient,
    GitSourceSpec,
)

#: The seam ``close()`` goes through, so its test can observe the removals
#: without creating trees a real apply would have made through the client.
_rmtree = shutil.rmtree


@dataclass
class SourceSession:
    """Per-apply: what ``from`` may name, what last time resolved, what this
    apply fetched. Created by the apply service at ``start_apply``/``dry_run``
    and closed in every terminal path — including launch failure."""

    #: The stored document's top-level ``sources`` map, frozen at apply start.
    sources: Mapping[str, Mapping[str, Any]]
    #: Named source → the SHA the last apply recorded (``ApplyReport.sources``
    #: read back). A source absent here has no strict opinion yet.
    baselines: Mapping[str, str]
    #: The git transport; injected so tests script it and production gets the
    #: subprocess client via the DI provider.
    git: GitSourceClient

    _checkouts: dict[tuple[str, str], GitCheckout] = field(default_factory=dict)
    _resolutions: list[SourceResolution] = field(default_factory=list)
    _recorded: set[str] = field(default_factory=set)

    def checkout(
        self,
        spec: GitSourceSpec,
        *,
        headers: Mapping[str, str],
        display: str,
        auth_name: Optional[str],
    ) -> GitCheckout:
        """The checkout for one ``(url, ref)`` — fetching only the first time.

        ``display`` is the report's name for the source: the declared ``from``
        name for a named source, the repository URL for an inline one — the
        same key the baselines are read back by, so strict mode and the
        report agree on identity.
        """
        key = (spec.url, spec.ref)
        checkout = self._checkouts.get(key)
        if checkout is None:
            checkout = self.git.fetch(spec, headers=dict(headers))
            self._checkouts[key] = checkout
        if display not in self._recorded:
            self._recorded.add(display)
            self._resolutions.append(
                SourceResolution(
                    name=display,
                    ref=spec.ref,
                    resolved_sha=checkout.sha,
                    auth=auth_name,
                )
            )
        return checkout

    def resolution_records(self) -> tuple[SourceResolution, ...]:
        """What the report's ``sources`` section will carry."""
        return tuple(self._resolutions)

    def baseline(self, display: str) -> Optional[str]:
        """The SHA the last apply resolved this source to, or ``None``."""
        return self.baselines.get(display)

    def close(self) -> None:
        """Remove every checkout's tree. Idempotent — every terminal path of
        an apply calls it, including the launch-failure one."""
        for checkout in self._checkouts.values():
            _rmtree(checkout.root, ignore_errors=True)
        self._checkouts.clear()


__all__ = ["SourceSession"]
