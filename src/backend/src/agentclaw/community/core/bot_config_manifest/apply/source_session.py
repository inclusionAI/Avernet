"""One apply's named-source state.

The four things a single apply needs and nothing more: the document's
``sources`` declarations, the strict-mode baselines read back — keyed on the
substituted ``(url, ref)`` — from the last apply that resolved that pair, a
checkout cache keyed the same way, and the :class:`SourceResolution` records
the report will carry. It hangs on ``ApplyContext`` beside ``budget``, mutable
by design inside a frozen context, because the fetcher is a DI singleton (state
there would leak across applies) and a re-resolution per entry would break "the
same ``(url, ref)`` is pulled once per apply".

A baseline is a fact about a **repository at a ref**, not about what the
document called it: strict mode asks "has this ``(url, ref)`` resolved
differently since we last stood behind it", and renaming a source, or pointing
one at another repository, does not make that a different question about the
same pair. Keying on the pair is also what makes the document the way out of a
strict refusal — editing ``ref`` asks about a pair no apply has an opinion on,
so a deliberate re-pin passes where a ref that moved underneath the same pair
still does not.

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
    #: ``(substituted repository url, ref)`` → the 40-character SHA the last
    #: apply that resolved that pair recorded. Read back out of
    #: ``ApplyReport.sources`` through the report history, off each row's
    #: ``url`` and ``ref``::
    #:
    #:     {
    #:         ("https://code.example.com/team/content.git", "v1.2.0"):
    #:             "4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
    #:         ("https://code.example.com/solo.git", "HEAD"): "9b1c...",
    #:     }
    #:
    #: **Not** keyed on the report's display name. The name is how a document
    #: refers to a source; the baseline answers "did this repository's ref move
    #: under us", and a rename, a re-pointed ``url`` or an edited ``ref`` all
    #: change the name's answer without changing the pair's.
    #:
    #: A pair absent here has no strict opinion yet, so strict mode admits its
    #: first resolution and ``keep_last`` has no baseline receipt to reuse —
    #: which is exactly what an edited ``ref`` produces, and why re-pinning the
    #: document is how a strict source is advanced.
    baselines: Mapping[tuple[str, str], str]
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
    #: idempotent per display, so ten entries naming one source produce one
    #: row — and two declarations of one repository produce two, which is what
    #: "one row per declaration" means::
    #:
    #:     {"content", "https://code.example.com/solo.git@main"}
    _recorded: set[str] = field(default_factory=set)

    def checkout(
        self,
        spec: GitSourceSpec,
        *,
        headers: Mapping[str, str],
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

        The report's name for the source plays no part here and nothing is
        recorded: a checkout answers "what does this ref name right now", and
        standing behind that answer is a separate event — see :meth:`adopt`.

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
            SourceResolution(name="content",
                             url="https://code.example.com/team/content.git",
                             ref="v1.2.0",
                             resolved_sha="4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
                             auth="git-prod")

        ``auth_name`` is the credential's **name** or ``None``, never a value.

        Called by the fetch pipeline only once the strict-mode gate has passed
        for the entry — a refused move is not adopted, so the report of a
        refusing (failed) apply carries no poisoned baseline, and the last
        apply's record keeps refusing the moved ref until the document is
        re-pinned. Idempotent per display; a display is a name or ``url@ref``,
        so every entry that names one source stands behind one resolution, and
        two names over one repository record one row each.
        """
        if display in self._recorded:
            return
        self._recorded.add(display)
        self._resolutions.append(
            SourceResolution(
                name=display,
                url=spec.url,
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

    def baseline(self, url: str, ref: str) -> Optional[str]:
        """The SHA the last apply that resolved this ``(url, ref)`` found, or
        ``None``.

        ``baseline("https://code.example.com/team/content.git", "v1.2.0")`` →
        ``"4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187"`` for a pair resolved
        before, ``None`` for one seen the first time — which includes a pair
        the document has just re-pinned onto.
        """
        return self.baselines.get((url, ref))

    def close(self) -> None:
        """Remove every checkout's temporary tree from disk and empty the
        cache. Idempotent — every terminal path of an apply calls it, including
        the launch-failure one. The resolution records survive: they are the
        report's, not the checkouts'."""
        for checkout in self._checkouts.values():
            _rmtree(checkout.root, ignore_errors=True)
        self._checkouts.clear()


__all__ = ["SourceSession"]
