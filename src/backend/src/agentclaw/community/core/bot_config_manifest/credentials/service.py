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
    RESERVED_TYPES,
    CredentialType,
    SourceCredentialRecord,
    SourceCredentialRow,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    PrefixAuthorizationPolicy,
    validate_prefixes,
)
from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.protocols.bot.source_credential import (
    SourceCredentialRepositoryProtocol,
)

#: RFC 7230 token — one line of authority, no hand-rolled parsing.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

_NAME_MAX = 128
_HEADER_NAME_MAX = 256  # 列宽同构;超长在边界拒绝,不是 DB 报错


class SourceCredentialService(SourceCredentialServiceProtocol):
    """Register, rotate, read (masked) and remove named tenant credentials."""

    @inject
    def __init__(
        self,
        repository: SourceCredentialRepositoryProtocol,
        vault: TokenVault,
        *,
        fail_closed: bool = False,
    ):
        # ``fail_closed`` is bound true only for profiles whose storage must
        # be ciphertext (see di module); singlebox/CI bind the default.
        self._repository = repository
        self._vault = vault
        self._fail_closed = fail_closed

    def put(
        self,
        *,
        name: str,
        header_name: str,
        secret: str,
        allowed_prefixes: list[str],
        owner_app_id: int,
        credential_type: CredentialType = CredentialType.HEADER,
        modifier: str = "",
    ) -> SourceCredentialRecord:
        if credential_type in RESERVED_TYPES:
            raise CredentialError(
                f"credential type {credential_type!r} is reserved, not supported"
            )
        if credential_type != CredentialType.HEADER:
            raise CredentialError("only header credentials are supported")
        if not name or len(name) > _NAME_MAX or any(c.isspace() for c in name):
            raise CredentialError("credential name must be a non-empty identifier")
        if not _HEADER_NAME_RE.fullmatch(header_name or ""):
            raise CredentialError("header_name is not a valid HTTP header name")
        if len(header_name) > _HEADER_NAME_MAX:
            raise CredentialError(
                f"header_name over {_HEADER_NAME_MAX} characters"
            )
        if not secret:
            raise CredentialError("secret must not be empty")
        if not isinstance(allowed_prefixes, list):
            raise CredentialError("allowed_prefixes must be a list")
        try:
            validate_prefixes(allowed_prefixes)
        except ValueError as exc:
            # 换分类学不换规则:policy 层的输入错误以服务的统一
            # CredentialError 面对外(一个家族,调用方一次捕获)。
            raise CredentialError(str(exc)) from exc

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
            header_name=header_name,
            allowed_prefixes=allowed_prefixes,
            secret_ciphertext=self._vault.encrypt(secret),
            owner_app_id=owner_app_id,
            modifier=modifier,
        )
        return self._masked(row)

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
            header_name=row.header_name,
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
        row = self._current()
        return {
            row.header_name: self._service._vault.decrypt_or_passthrough(
                row.secret_ciphertext
            )
        }

    def reauthorize(self, url) -> None:
        row = self._current()
        PrefixAuthorizationPolicy(
            self.name, json.loads(row.allowed_prefixes)
        ).reauthorize(url)
