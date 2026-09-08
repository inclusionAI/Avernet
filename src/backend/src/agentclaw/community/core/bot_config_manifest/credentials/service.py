"""Tenant source-credential service (W3, #1471) and the W2 fetcher binding.

Storage validation, rotation, masked reads, and the fail-closed guard live
here; the prefix boundary itself lives in ``policy.py``; the vault in
``bot_management.token_vault``.

The fail-closed guard deserves its restating here: ``TokenVault`` falls
through to *plaintext* when no master key resolved — right for singlebox,
catastrophic for tenant tokens in production (one keystore misconfiguration
= every tenant's tokens in the clear). The guard refuses the write before
any persistence under fail-closed profiles; the decision is injected via
DI (``SourceCredentialServiceBoot``), never a per-call argument.

Ownership is this layer's boundary: the creating application owns the
name, rotation and delete are its calls alone, and reads belong to every
application of the tenant (the name is the reference namespace manifests
cite). The check runs against the row, before any storage writes — a
non-owner's re-PUT must leave the stored value untouched.
"""
from __future__ import annotations

import json
import re

from injector import inject

from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
    CredentialNotFoundError,
    CredentialNotOwnedError,
    MasterKeyUnavailableError,
)
from agentclaw.community.core.bot_config_manifest.credentials.models import (
    EXCLUSIVE_FIELDS_BY_TYPE,
    REQUIRED_FIELDS_BY_TYPE,
    RESERVED_TYPES,
    CredentialType,
    SourceCredentialRecord,
    SourceCredentialRow,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    PrefixAuthorizationError,
    PrefixAuthorizationPolicy,
    validate_prefixes,
)
from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    endpoint_refusal,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import Resolver
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.plugin_api.object_store_client import ObjectStoreTarget
from agentclaw.community.core.repository.protocols.bot.source_credential import (
    SourceCredentialRepositoryProtocol,
)

#: RFC 7230 token — one line of authority, no hand-rolled parsing.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

_NAME_MAX = 128
_HEADER_NAME_MAX = 256  # 列宽同构;超长在边界拒绝,不是 DB 报错
_ACCESS_KEY_ID_MAX = 256  # 同上:列宽即边界
_REGION_MAX = 64
_ENDPOINT_MAX = 512  # 同上:列宽即边界


class SourceCredentialService(SourceCredentialServiceProtocol):
    """Register, rotate, read (masked) and remove named tenant credentials."""

    @inject
    def __init__(
        self,
        repository: SourceCredentialRepositoryProtocol,
        vault: TokenVault,
        *,
        fail_closed: bool = False,
        endpoint_allow_hosts: tuple[str, ...] = (),
        endpoint_resolver: "Resolver | None" = None,
    ):
        # ``fail_closed`` is bound true only for profiles whose storage must
        # be ciphertext (see di module); singlebox/CI bind the default.
        self._repository = repository
        self._vault = vault
        self._fail_closed = fail_closed
        # The deployment's transport allowlist — the same value the guarded
        # fetcher takes, so a legitimately internal object store is declared
        # in one place rather than two that can disagree.
        self._endpoint_allow_hosts = endpoint_allow_hosts
        # Address resolution seam, the same one the guarded fetcher takes:
        # real DNS in production, scripted in tests — including the
        # check-public/connect-private rebinding pair, which is only
        # expressible with an injected resolver.
        self._endpoint_resolver = endpoint_resolver

    def put(
        self,
        *,
        name: str,
        secret: str,
        allowed_prefixes: list[str],
        owner_app_id: int,
        header_name: str | None = None,
        access_key_id: str | None = None,
        region: str | None = None,
        endpoint: str | None = None,
        credential_type: CredentialType = CredentialType.HEADER,
        modifier: str = "",
    ) -> SourceCredentialRecord:
        if credential_type in RESERVED_TYPES:
            raise CredentialError(
                f"credential type {credential_type!r} is reserved, not supported"
            )
        try:
            # Normalised before anything reads it as a mechanism. The router
            # hands over a validated enum, but this service is also called
            # directly, and a bare string reaching the per-mechanism rules
            # below would take the wrong branch rather than be refused.
            credential_type = CredentialType(credential_type)
        except ValueError as exc:
            raise CredentialError(
                f"unknown credential type {credential_type!r}; this build "
                "supports "
                + ", ".join(
                    repr(t.value)
                    for t in CredentialType
                    if t not in RESERVED_TYPES
                )
            ) from exc
        if not name or len(name) > _NAME_MAX or any(c.isspace() for c in name):
            raise CredentialError("credential name must be a non-empty identifier")
        if not secret:
            raise CredentialError("secret must not be empty")
        self._check_mechanism_fields(
            credential_type,
            header_name=header_name,
            access_key_id=access_key_id,
            region=region,
            endpoint=endpoint,
        )
        if credential_type is CredentialType.HEADER:
            if not _HEADER_NAME_RE.fullmatch(header_name or ""):
                raise CredentialError("header_name is not a valid HTTP header name")
            if len(header_name or "") > _HEADER_NAME_MAX:
                raise CredentialError(
                    f"header_name over {_HEADER_NAME_MAX} characters"
                )
        elif len(access_key_id or "") > _ACCESS_KEY_ID_MAX:
            raise CredentialError(
                f"access_key_id over {_ACCESS_KEY_ID_MAX} characters"
            )
        if region is not None and len(region) > _REGION_MAX:
            raise CredentialError(f"region over {_REGION_MAX} characters")
        if endpoint is not None:
            if len(endpoint) > _ENDPOINT_MAX:
                raise CredentialError(
                    f"endpoint over {_ENDPOINT_MAX} characters"
                )
            # Validated BEFORE it is stored. The object-store road drops the
            # guarded fetcher's SSRF machinery because its endpoint comes off
            # a credential rather than out of a tenant's document — but the
            # credential is itself written by an authenticated tenant
            # application through this very method, so "not from the
            # document" was never "not from the tenant". Without this,
            # ``http://169.254.169.254/`` is a storable endpoint and the
            # platform connects to it on the next apply.
            #
            # Refusing at write rather than at read is deliberate: a bad
            # endpoint that reaches storage fails every apply that cites it,
            # and the person who can fix it is the one making this call.
            refusal = endpoint_refusal(
                endpoint,
                allow_hosts=self._endpoint_allow_hosts,
                resolver=self._endpoint_resolver,
            )
            if refusal is not None:
                raise CredentialError(refusal)
        if not isinstance(allowed_prefixes, list):
            raise CredentialError("allowed_prefixes must be a list")
        if credential_type is CredentialType.HEADER:
            # Mandatory for ``header`` and only for it. That mechanism puts a
            # secret on the wire to a URL the tenant's document names, so the
            # prefix boundary is the only thing deciding which hosts may
            # receive it. ``oss_aksk`` has no tenant-supplied host at all —
            # the endpoint comes off the credential row — so requiring
            # prefixes there would be a boundary drawn around nothing, and
            # callers would satisfy it with a placeholder that governs
            # nothing. The constraint moves to where it bites.
            try:
                validate_prefixes(allowed_prefixes)
            except ValueError as exc:
                # 换分类学不换规则:policy 层的输入错误以服务的统一
                # CredentialError 面对外(一个家族,调用方一次捕获)。
                raise CredentialError(str(exc)) from exc
        elif allowed_prefixes:
            # Not silently dropped: a caller who wrote prefixes on an
            # object-store credential believes they constrained something.
            raise CredentialError(
                "'allowed_prefixes' constrains the URL a 'header' credential "
                "is presented to; an 'oss_aksk' credential reads the endpoint "
                "from its own row, so there is no tenant-supplied host to "
                "constrain — leave it empty"
            )

        if self._fail_closed and not self._vault.has_master_key:
            raise MasterKeyUnavailableError(
                "credential storage requires a configured master key in this profile"
            )

        # The owner gate — before any storage write. Rotation is a re-PUT,
        # and a re-PUT by any application other than the name's owner is a
        # cross-application hijack of every manifest citation of that name
        # (whole-row replace: secret, header, and prefixes), so it is
        # refused here rather than stamped over.
        existing = self._repository.get(name=name)
        if existing is not None and existing.owner_app_id != owner_app_id:
            raise CredentialNotOwnedError(
                f"credential {name!r} is owned by another application"
            )

        row = self._repository.upsert(
            name=name,
            credential_type=credential_type,
            # The column is NOT NULL and an ``oss_aksk`` credential presents
            # no header at all, so the empty string is the storage form of
            # "none" rather than a header named "". The
            # public record turns it back into ``None`` — nothing downstream
            # ever sees an empty header name and wonders whether to present it.
            header_name=header_name or "",
            access_key_id=access_key_id,
            region=region,
            endpoint=endpoint,
            allowed_prefixes=allowed_prefixes,
            secret_ciphertext=self._vault.encrypt(secret),
            owner_app_id=owner_app_id,
            modifier=modifier,
        )
        return self._masked(row)

    @staticmethod
    def _check_mechanism_fields(
        credential_type: CredentialType,
        *,
        header_name: str | None,
        access_key_id: str | None,
        region: str | None,
        endpoint: str | None,
    ) -> None:
        """Every field belongs to a mechanism; a mismatch is refused, not dropped.

        Both directions, and the second is the one that matters. A missing
        required field fails loudly the first time it is used anyway. A field
        sent to the *wrong* mechanism would be silently dropped, and the caller
        would go on believing they had pointed a credential at an object store
        when it in fact presents a header to a URL — which is a security
        expectation, not a typo.
        """
        supplied = {
            "header_name": header_name,
            "access_key_id": access_key_id,
            "region": region,
            "endpoint": endpoint,
        }
        for field in sorted(REQUIRED_FIELDS_BY_TYPE.get(credential_type, ())):
            if not supplied.get(field):
                raise CredentialError(
                    f"a {credential_type.value!r} credential requires "
                    f"{field!r}"
                )
        mine = EXCLUSIVE_FIELDS_BY_TYPE.get(credential_type, frozenset())
        for other, fields in EXCLUSIVE_FIELDS_BY_TYPE.items():
            if other is credential_type:
                continue
            for field in sorted(fields - mine):
                if supplied.get(field):
                    raise CredentialError(
                        f"{field!r} belongs to a {other.value!r} credential "
                        f"and is not valid on a {credential_type.value!r} one"
                    )

    def get(self, *, name: str) -> SourceCredentialRecord:
        return self._masked(self._row_or_404(name))

    def list_credentials(self) -> list[SourceCredentialRecord]:
        return [self._masked(row) for row in self._repository.list()]

    def delete(self, *, name: str, caller_app_id: int) -> bool:
        row = self._repository.get(name=name)
        if row is None:
            return False
        if row.owner_app_id != caller_app_id:
            raise CredentialNotOwnedError(
                f"credential {name!r} is owned by another application"
            )
        return self._repository.delete(name=name)

    def binding(self, *, name: str) -> SourceCredentialBinding:
        self._row_or_404(name)
        return SourceCredentialBinding(self, name)

    # --- internals ----------------------------------------------------------

    def _row_or_404(self, name: str) -> SourceCredentialRow:
        row = self._repository.get(name=name)
        if row is None:
            raise CredentialNotFoundError(f"credential {name!r} does not exist")
        return row

    @staticmethod
    def _prefixes_of(row: SourceCredentialRow) -> list[str]:
        return json.loads(row.allowed_prefixes)

    def _masked(self, row: SourceCredentialRow) -> SourceCredentialRecord:
        return SourceCredentialRecord(
            id=row.id,
            name=row.name,
            credential_type=row.credential_type,
            header_name=row.header_name or None,
            # Readable back, and only this half: the key id is an identifier
            # that rotation cannot be operated blind, while
            # ``secret_ciphertext`` still has no representation here at all —
            # the two records exist so that is true by construction.
            access_key_id=row.access_key_id,
            region=row.region,
            endpoint=row.endpoint,
            allowed_prefixes=self._prefixes_of(row),
            has_secret=bool(row.secret_ciphertext),
            owner_app_id=row.owner_app_id,
            updated_at=row.gmt_modified,
        )


class SourceCredentialBinding:
    """The W2 fetcher seams for one named credential.

    Duck-satisfies the guarded fetcher's ``CredentialInjector`` /
    ``AuthorizationPolicy`` protocols (structural typing — no import of the
    fetcher module; whichever of W2/W3 lands first, this binds at use):
    ``headers_for(url) -> {header: value}`` and ``reauthorize(url) -> None``
    (raising to refuse the hop).

    Both methods re-read the row per call, on purpose: rotation is a re-PUT
    with no signal, and "next fetch uses the new value" is the observable
    rotation contract. The credential name rides errors — the value never
    does, in any message raised here.
    """

    def __init__(self, service: SourceCredentialService, name: str) -> None:
        self._service = service
        self.name = name

    def _current(self) -> SourceCredentialRow:
        row = self._service._repository.get(name=self.name)
        if row is None:
            raise CredentialNotFoundError(
                f"credential {self.name!r} was deleted; references fail next apply"
            )
        return row

    def headers_for(self, url) -> dict[str, str]:
        """What this credential presents for this URL.

        One mechanism may reach here: ``header``, which puts the secret on the
        wire under the caller's header name. ``oss_aksk`` never does — an
        object store is read through its own client, handed the key pair
        directly rather than a set of headers computed here.

        **The type is checked, not assumed.** Nothing ties a source's
        ``protocol`` to its credential's ``credential_type``, so a
        ``protocol: git`` source may legally name an ``oss_aksk`` credential
        and the git road will ask that binding for headers. Answering would
        return ``{"": <the object store's secret key>}`` — the empty string
        being the stored "no header" sentinel — and send it to whatever git
        host the document named. The mirror of ``object_store_target``'s own
        guard, and the direction that was missing.
        """
        row = self._current()
        if row.credential_type is not CredentialType.HEADER:
            raise CredentialError(
                f"credential {self.name!r} is a {row.credential_type.value!r} "
                "credential and presents no header; a source that fetches over "
                f"HTTP needs one of type {CredentialType.HEADER.value!r}"
            )
        return {
            row.header_name: self._service._vault.decrypt_or_passthrough(
                row.secret_ciphertext
            )
        }

    def object_store_target(self, bucket: str) -> "ObjectStoreTarget":
        """The address and identity for reading ``bucket`` as this credential.

        The endpoint, the region and both halves of the key pair come off the
        row; only the bucket comes from the caller. That split is the whole
        security property of this road: a tenant's document chooses *what* to
        read and never *where from*, so there is no host for it to point a
        credential at.

        The secret is decrypted here and lives only on the returned value —
        which redacts it in ``repr`` for the traceback case.
        """
        row = self._current()
        if row.credential_type is not CredentialType.OSS_AKSK:
            raise CredentialError(
                f"credential {self.name!r} is a {row.credential_type.value!r} "
                "credential; an object store source needs one of type "
                f"{CredentialType.OSS_AKSK.value!r}"
            )
        return ObjectStoreTarget(
            endpoint=row.endpoint or "",
            bucket=bucket,
            access_key_id=row.access_key_id or "",
            secret_access_key=self._service._vault.decrypt_or_passthrough(
                row.secret_ciphertext
            ),
            region=row.region,
        )

    def reauthorize(self, url) -> None:
        """Refuse the hop unless the URL is inside this credential's prefixes.

        The ``ValueError`` catch is not defensive noise. ``oss_aksk`` rows now
        legitimately store an empty prefix list — there is no tenant-supplied
        host on that road for a prefix to constrain — and a ``git`` source may
        name one, at which point this runs against ``[]`` and
        ``validate_prefixes`` answers with a bare ``ValueError``. That is in no
        family the fetch pipeline catches, so it escaped ``resolve`` and
        crashed the whole apply rather than failing one entry. The
        ``credential_type`` guard on ``headers_for`` closes the path that gets
        here; this makes the class of mistake survivable rather than fatal, so
        the next stored value nobody anticipated fails one entry with a reason.
        """
        row = self._current()
        try:
            PrefixAuthorizationPolicy(
                self.name, json.loads(row.allowed_prefixes)
            ).reauthorize(url)
        except PrefixAuthorizationError:
            raise
        except (ValueError, TypeError) as exc:
            raise CredentialError(
                f"credential {self.name!r} has no usable 'allowed_prefixes', "
                f"so it cannot authorize a fetch: {exc}"
            ) from exc
