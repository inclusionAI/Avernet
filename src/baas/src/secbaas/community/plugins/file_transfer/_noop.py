"""No-op FileTransferBackend for stub/singlebox/test modes.

Provides a safe zero-value that allows the DI container to resolve
without real OSS credentials.  All operations raise NotImplementedError
with a clear message — the caller is responsible for guarding with
feature-flag checks before invoking transfer operations.
"""

from secbaas.community.api.session_file_sharing import (
    SessionFileTransferProxyUnavailableError,
)
from secbaas.community.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    PartInfo,
    SessionFileUrlProjector,
)

_DISABLED_MESSAGE = "file_transfer is disabled in this deployment"


class NoopFileTransferBackend(FileTransferBackend):
    """No-op implementation for when file transfer is disabled.

    Used in stub/singlebox mode where OSS credentials are not available.
    The DI container resolves this safely; any actual transfer operation
    raises NotImplementedError.
    """

    @property
    def disabled(self) -> bool:
        """Report that this no-op backend is disabled."""
        return True

    def generate_upload_url(
        self, staging_path: str, expire_seconds: int, content_type: str | None = None
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def check_object_exists(self, staging_path: str) -> bool:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def generate_download_url(
        self,
        staging_path: str,
        expire_seconds: int,
        response_params: dict | None = None,
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def initiate_multipart_upload(
        self,
        staging_path: str,
        expire_seconds: int,
        part_count: int = 2,
        content_type: str | None = None,
    ) -> MultipartSession:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def list_parts(self, staging_path: str, session_id: str) -> list[PartInfo]:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def complete_multipart_upload(
        self, staging_path: str, session_id: str, parts: list[PartInfo]
    ) -> None:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def delete_object(self, key: str) -> None:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def build_staging_path(
        self,
        tenant: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def build_session_staging_path(
        self,
        tenant: str,
        session_id: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)


class NoopSessionFileUrlProjector(SessionFileUrlProjector):
    """Identity projection default for non-proxied deployments.

    Returns every URL unchanged (D-01: main-site behavior is byte-identical
    to today — bare OSS URLs).  This is the second half of the D-06
    two-sided guard: if ``deploy_tenant`` is aliyun but the stub
    projector is selected, the configuration is contradictory and
    projection must refuse with a 503 instead of leaking a bare OSS URL
    in that tenant.
    """

    def __init__(self, deploy_tenant: str = ""):
        self._deploy_tenant = deploy_tenant

    def project(self, url: str) -> str:
        if self._deploy_tenant == "aliyun":
            raise SessionFileTransferProxyUnavailableError(
                reason=(
                    "session_file_url_projector selector 'stub' is selected "
                    "while deploy_tenant is aliyun — configure "
                    "plugins.session_file_url_projector: 'aliyun_ack'"
                )
            )
        return url
