"""One apply's named-source state (W7, #1475).

The four things a single apply needs and nothing more: the document's
``sources`` declarations, the strict-mode baselines read back from the last
apply that resolved each source, a checkout cache keyed on the substituted
``(url, ref)``, and the `SourceResolution` records the report will carry. It
hangs on ``ApplyContext`` beside ``budget`` — mutable by design inside a frozen
context, the precedent that ruling cites — because the fetcher is a DI
singleton (state there would leak across applies) and a re-resolution per
entry would break "the same {git, ref} is pulled once per apply".

A checkout and its resolution are **deliberately two events**. Fetching the
tree answers "what does the ref name right now"; adopting it answers "and this
apply stands behind that answer" — the strict-mode refusal sits between the
two, so a refused entry records nothing and the baseline it was checked
against survives to refuse the next apply too. A fetch that failed outright
adopts nothing either: the report of a failed apply carries no resolution for
that source, and the baselines the next apply reads are drawn from the last
apply that *did* resolve it (a bounded walk back through report history in
the apply service), so one network outage cannot silently disarm strict mode
or the ``keep_last`` baseline receipt.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
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
    #: Named source → the SHA the last apply that resolved it recorded
    #: (``ApplyReport.sources`` read back through the report history). A
    #: source absent here has no strict opinion yet.
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
    ) -> "tuple[GitCheckout, bool]":
        """The checkout for one ``(url, ref)``, fetching only the first time.

        Returns the checkout and whether **this call** did the fetching (a
        cache hit answers ``False`` — those bytes were charged when they were
        actually moved). ``display`` is the report's name for the source: the
        declared ``from`` name for a named source, the repository URL for an
        inline one — the same key the baselines are read back by, so strict
        mode and the report agree on identity. Nothing is recorded here: see
        :meth:`adopt`.
        """
        key = (spec.url, spec.ref)
        checkout = self._checkouts.get(key)
        if checkout is None:
            checkout = self.git.fetch(spec, headers=dict(headers))
            self._checkouts[key] = checkout
            return checkout, True
        return checkout, False

    def adopt(
        self,
        *,
        display: str,
        spec: GitSourceSpec,
        checkout: GitCheckout,
        auth_name: Optional[str],
    ) -> None:
        """Record that this apply stands behind ``display`` → ``checkout.sha``.

        Called by the fetch pipeline only once the strict-mode gate has
        passed for the entry — a refused move is not adopted, so the report
        of a refusing (failed) apply carries no poisoned baseline, and the
        last apply's record keeps refusing the moved ref until the document
        is re-pinned. Idempotent per display: every entry that names the
        source stands behind the same resolution.
        """
        if display in self._recorded:
            return
        self._recorded.add(display)
        self._resolutions.append(
            SourceResolution(
                name=display,
                ref=spec.ref,
                resolved_sha=checkout.sha,
                auth=auth_name,
            )
        )

    def resolution_records(self) -> tuple[SourceResolution, ...]:
        """What the report's ``sources`` section will carry."""
        return tuple(self._resolutions)

    def baseline(self, display: str) -> Optional[str]:
        """The SHA the last apply that resolved this source found, or ``None``."""
        return self.baselines.get(display)

    def close(self) -> None:
        """Remove every checkout's tree. Idempotent — every terminal path of
        an apply calls it, including the launch-failure one."""
        for checkout in self._checkouts.values():
            _rmtree(checkout.root, ignore_errors=True)
        self._checkouts.clear()


__all__ = ["SourceSession"]
