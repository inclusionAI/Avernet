"""One apply's named-source state.

The four things a single apply needs and nothing more: the document's
``sources`` declarations, the strict-mode baselines read back from the last
apply that resolved each source, a checkout cache keyed on the substituted
``(url, ref)``, and the :class:`SourceResolution` records the report will
carry. It hangs on ``ApplyContext`` beside ``budget``, mutable by design inside
a frozen context, because the fetcher is a DI singleton (state there would leak
across applies) and a re-resolution per entry would break "the same
``(url, ref)`` is pulled once per apply".

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
    apply fetched.

    Created by: ``services/config_manifest_apply_service``, at ``start_apply``
    and ``dry_run``, and closed in every terminal path including launch
    failure.
    Consumed by: ``apply/source_fetchers`` (``GitSourceFetcher.fetch``,
    ``GitSourceFetcher._keep_last``) and ``apply/source_resolver``
    (``resolve``, ``declared_protocol``).
    """

    #: The stored document's top-level ``sources`` map, verbatim and frozen at
    #: apply start: declared name → the raw declaration mapping, unparsed.
    #: ``resolve`` looks an entry's ``from`` name up here and hands the
    #: value to ``parse_source``::
    #:
    #:     {
    #:         "content": {
    #:             "protocol": "git",
    #:             "url": "https://code.example.com/team/content.git",
    #:             "ref": "v1.2.0",
    #:             "subpath": "kb",
    #:             "auth": "git-prod",
    #:         },
    #:         "artifacts": {
    #:             "protocol": "oss",
    #:             "bucket": "team-artifacts",
    #:             "key": "tools/",
    #:             "auth": "oss-prod",
    #:         },
    #:     }
    #:
    #: Empty when the document declares no ``sources``; an entry naming one
    #: then fails with "is not declared under 'sources'".
    sources: Mapping[str, Mapping[str, Any]]
    #: Display name → the 40-character SHA the last apply that resolved that
    #: source recorded. Read back out of ``ApplyReport.sources`` through the
    #: report history, so the keys are ``SourceResolution.name`` values: the
    #: declared ``from`` name for a named source, the repository URL for an
    #: inline one::
    #:
    #:     {
    #:         "content": "4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
    #:         "https://code.example.com/solo.git": "9b1c...",
    #:     }
    #:
    #: A source absent here has no strict opinion yet, so strict mode admits
    #: its first resolution and ``keep_last`` has no baseline receipt to reuse.
    baselines: Mapping[str, str]
    #: The git transport; injected so tests script it and production gets the
    #: subprocess client via the DI provider.
    git: GitSourceClient

    #: ``(substituted repository url, ref)`` → the checkout that pair produced.
    #: The key deliberately excludes ``subpath``, which is what lets two
    #: entries reading different paths out of one commit share one fetch::
    #:
    #:     {
    #:         ("https://code.example.com/team/content.git", "v1.2.0"):
    #:             GitCheckout(root=..., sha="4f2a9c1b...", ...),
    #:     }
    #:
    #: Both of these entries hit that one key, and only the first fetches::
    #:
    #:     - path: data/faq.csv
    #:       from: content
    #:       subpath: faq.csv        # composes to "kb/faq.csv"
    #:     - path: data/prices/
    #:       from: content
    #:       subpath: prices         # composes to "kb/prices"
    #:
    #: The url is the **substituted** one (``${BOT_*}`` already resolved), so
    #: two entries whose urls differ only before substitution still share.
    _checkouts: dict[tuple[str, str], GitCheckout] = field(default_factory=dict)
    #: The report's ``sources`` rows, in the order they were adopted.
    _resolutions: list[SourceResolution] = field(default_factory=list)
    #: The display names already in ``_resolutions``. Makes :meth:`adopt`
    #: idempotent per source, so ten entries naming one source produce one row::
    #:
    #:     {"content", "https://code.example.com/solo.git"}
    _recorded: set[str] = field(default_factory=set)

    def checkout(
        self,
        spec: GitSourceSpec,
        *,
        headers: Mapping[str, str],
        display: str,
    ) -> "tuple[GitCheckout, bool]":
        """The checkout for one ``(url, ref)``, fetching only the first time.

        Returns ``(checkout, fetched_here)``::

            # first entry naming this source
            (GitCheckout(root=PosixPath("/tmp/acm-git-x9"),
                         sha="4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
                         url="https://code.example.com/team/content.git",
                         ref="v1.2.0",
                         members=(("100644", "kb/faq.csv", 812), ...)),
             True)

            # a later entry on the same (url, ref)
            (<the same GitCheckout>, False)

        ``fetched_here`` is what the caller charges the apply budget on: a
        cache hit answers ``False``, because those bytes were charged when they
        actually moved.

        ``display`` is the report's name for the source — the declared ``from``
        name, or the repository URL for an inline one — and is the same key
        ``baselines`` is read by, so strict mode and the report agree on
        identity. It is not part of the cache key and nothing is recorded here:
        see :meth:`adopt`.

        ``headers`` carries the credential's injected headers, or is empty for
        an anonymous fetch.
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

        Appends one :class:`SourceResolution` and returns nothing::

            adopt(display="content",
                  spec=GitSourceSpec(url="https://code.example.com/team/"
                                         "content.git", ref="v1.2.0", ...),
                  checkout=<the checkout>,
                  auth_name="git-prod")

            # _resolutions now ends with
            SourceResolution(name="content", ref="v1.2.0",
                             resolved_sha="4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
                             auth="git-prod")

        ``auth_name`` is the credential's **name** or ``None``, never a value.

        Called by the fetch pipeline only once the strict-mode gate has passed
        for the entry — a refused move is not adopted, so the report of a
        refusing (failed) apply carries no poisoned baseline, and the last
        apply's record keeps refusing the moved ref until the document is
        re-pinned. Idempotent per display: every entry that names the source
        stands behind the same resolution.
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
        """What the report's ``sources`` section will carry — one row per
        distinct ``display``, adoption order. Empty when this apply resolved no
        git source, which includes every object-store-only document."""
        return tuple(self._resolutions)

    def baseline(self, display: str) -> Optional[str]:
        """The SHA the last apply that resolved this source found, or ``None``.

        ``baseline("content")`` → ``"4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187"``
        for a source resolved before, ``None`` for one seen the first time.
        """
        return self.baselines.get(display)

    def close(self) -> None:
        """Remove every checkout's temporary tree from disk and empty the
        cache. Idempotent — every terminal path of an apply calls it, including
        the launch-failure one. The resolution records survive: they are the
        report's, not the checkouts'."""
        for checkout in self._checkouts.values():
            _rmtree(checkout.root, ignore_errors=True)
        self._checkouts.clear()


__all__ = ["SourceSession"]
