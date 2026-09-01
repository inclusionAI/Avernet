"""One entry's fetch, from a declared source to materialisable bytes.

The second half of ``resolve`` for every fetch-consuming category: substitute
``${BOT_*}``, consult the platform's own copy (W11) before the network, fetch
through the guarded transport (W2) under a named credential (W3), and file the
result with the content store so delivery and audit share one copy (§2.8).

Fetch lives here **and only here** — the registry's contract says ``resolve``
is where a category's failures are collected before anything is written, and a
fetch is exactly that kind of failure. Materialisers translate :class:`EntryFetchError`
into their ``ResolveFailure`` currency; nothing about the transport leaks out.
Because the fetch belongs to ``resolve``, a ``dry_run`` may perform one (it
still writes **nothing to the bot** — the store it files with is the
platform's own record of what a bot was served, true whether or not the apply
proceeds).

Pinned vs unpinned is a declared-digest question, and it decides the
store-first vs fetch-first policy:

* **pinned** (the entry declares a ``digest``) — a receipt for this bot and
  source URL *matching the declaration* is the bytes, by content addressing:
  no network is consulted to acquire what the platform already holds. A
  source that has since moved is irrelevant — the declaration asks for the
  pinned bytes, and a re-fetch of them would only fail the pin.
* **unpinned** — the entry wants whatever is there *now*; every apply
  re-fetches so it converges to the source, and ``keep_last`` exists for the
  day that fetch fails.

``keep_last`` (on a real fetch failure) reads the latest receipt: bytes that
are entitled to be reused when the declaration pinned nothing, and bytes that
must match the pin when it did — a receipt that disagrees with a declared
digest is not "last", it is stale, and supplying it would silently pin bytes
the declaration never named.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.bot_config_manifest.content.models import (
    ContentScope,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
    FetchedObject,
    GuardedFetcher,
    FetchRequest,
)
from agentclaw.community.core.bot_config_manifest.schema import placeholders
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.context import (
        ApplyContext,
    )
    from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
        ManifestContentServiceProtocol,
    )
    from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
        SourceCredentialServiceProtocol,
    )

logger = get_logger()


class EntryFetchError(Exception):
    """One entry's bytes could not be acquired, with a report-safe reason.

    The reason is built from the transport's and the credential service's own
    words — W2 refuses before sending anything that would carry caller or
    source data, and W3's error family names credentials without ever carrying
    the value — so a secret cannot ride out of this module inside an
    exception. Materialisers hand ``reason`` to ``ResolveFailure`` verbatim.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FetchedEntry:
    """One entry's bytes, their content address, and where they came from."""

    content: bytes
    digest: str
    #: True when the platform's own copy answered — no network was touched.
    from_store: bool
    content_type: Optional[str] = None


def scope_of(ctx: "ApplyContext") -> ContentScope:
    """The store scope for the bot an apply runs against.

    The three axes the bot record already carries — the same scope every store
    event for this apply is filed under, so the receipts this pipeline reads
    and the ones it writes are one log.
    """
    return ContentScope(env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id)


class EntryFetcher:
    """Fetches one manifest entry's bytes on a bot's behalf.

    Composed once per apply — the transport is stateless per hop, so there is
    nothing to hold between entries — and handed to every materialiser that
    fetches. One funnel for W5's two categories and W6's ``resources`` when it
    arrives; a category that bypassed it would acquire unrecorded bytes, and
    §2.8's audit and ``keep_last`` both read from exactly this log.
    """

    def __init__(
        self,
        fetcher: GuardedFetcher,
        content: "ManifestContentServiceProtocol",
        credentials: "SourceCredentialServiceProtocol",
    ) -> None:
        self._fetcher = fetcher
        self._content = content
        self._credentials = credentials

    def fetch(
        self,
        ctx: "ApplyContext",
        *,
        source_url: str,
        digest: Optional[str] = None,
        auth: Optional[str] = None,
        category: str = "resources_file",
        keep_last: bool = False,
    ) -> FetchedEntry:
        """Acquire one entry's bytes. Raises :class:`EntryFetchError`.

        ``${BOT_*}`` substitution happens **before** the fetch and therefore
        before prefix authorization: the W3 policy re-authorises every hop
        against the URL the request will actually name, so a substituted URL
        cannot steer the request outside its credential's prefixes (or inside
        them, unseen).
        """
        target = _substitute(ctx, source_url)
        scope = scope_of(ctx)
        receipt = self._content.latest_receipt(scope, source_url=target)

        if digest is not None and receipt is not None and receipt.digest == digest:
            # Pinned and in the platform's copy: content addressing makes the
            # stored bytes *the* declared bytes. Serving them is not a cache
            # nicety — a re-fetch of a pin can only succeed by redownloading
            # the same bytes, or fail loudly, so the store is the strictly
            # more available source of the same truth.
            return FetchedEntry(
                content=self._content.read(digest),
                digest=digest,
                from_store=True,
                content_type=receipt.content_type,
            )

        try:
            fetched = self._fetch(ctx, target=target, digest=digest, auth=auth,
                                  category=category)
        except (FetchFailedError, FetchRefusedError) as exc:
            if (
                keep_last
                and receipt is not None
                and (digest is None or receipt.digest == digest)
            ):
                logger.info(
                    "[manifest.fetch] keep_last reused the platform copy, "
                    "url_host=%s, digest=%s",
                    target.rpartition("//")[2].partition("/")[0],
                    receipt.digest,
                )
                return FetchedEntry(
                    content=self._content.read(receipt.digest),
                    digest=receipt.digest,
                    from_store=True,
                    content_type=receipt.content_type,
                )
            raise EntryFetchError(str(exc)) from exc
        except CredentialError as exc:
            raise EntryFetchError(str(exc)) from exc

        self._content.store(
            fetched,
            scope=scope,
            source_url=target,
            credential_name=auth,
            modifier=ctx.actor_id,
        )
        return FetchedEntry(
            content=fetched.bytes,
            digest=fetched.sha256,
            from_store=False,
            # The object's own content type is what the source served; the
            # receipt's is the width-checked column copy of the same. Either
            # may be None, and the caller's archive detection treats that as
            # "the source did not say".
            content_type=fetched.content_type,
        )

    def _fetch(
        self,
        ctx: "ApplyContext",
        *,
        target: str,
        digest: Optional[str],
        auth: Optional[str],
        category: str,
    ) -> FetchedObject:
        """One guarded request, carrying a named credential if one is declared.

        The binding object satisfies both of the fetcher's seams — headers to
        present, and per-hop re-authorization — which is W3's composition: the
        same binding refreshes from the stored row on every hop, so rotation
        lands on the very next fetch with no signal needed.
        """
        binding = self._credentials.binding(name=auth) if auth else None
        return self._fetcher.fetch(
            FetchRequest(
                url=target,
                expected_digest=digest,
                category=category,
                injector=binding,
                policy=binding,
            )
        )


def _substitute(ctx: "ApplyContext", source_url: str) -> str:
    """``${BOT_*}`` in a source URL, against this apply's deployment context.

    Unknown names are left untouched by the resolver itself — they cannot
    reach here through a stored document, because the write path refuses
    them — and a visible leftover in a fetch URL makes that bug findable
    instead of fetching something plausible-looking.
    """
    return placeholders.resolve(
        source_url,
        engine_type=ctx.engine_type,
        env=ctx.env,
        tenant=ctx.tenant,
    )


__all__ = ["EntryFetchError", "EntryFetcher", "FetchedEntry", "scope_of"]
