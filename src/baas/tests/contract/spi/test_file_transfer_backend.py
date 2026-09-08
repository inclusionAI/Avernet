"""Conformance contract for FileTransferBackend implementations.

Exercises both shipped implementations — NoopFileTransferBackend and
AliyunOssFileTransferBackend — through the FileTransferBackend protocol
surface (Rule 25).  Contract tests stay at interface level: return
shapes, staging-path patterns, traversal guards, and the disabled
semantics; the byte-level oss2 call contracts live in the unit matrix.
"""

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.bootstrap._configs import FileTransferOssConfigSchema
from secbaas.community.plugins.file_transfer import (
    AliyunOssFileTransferBackend,
    NoopFileTransferBackend,
)
from secbaas.community.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    PartInfo,
)

_DISABLED_MESSAGE = "file_transfer is disabled in this deployment"


class FileTransferBackendContract:
    """Abstract conformance contract for FileTransferBackend implementations.

    Every implementation must pass these tests; a no-op implementation
    still satisfies them because ``disabled`` short-circuits the
    behavior-bearing member called in each test.
    """

    plugin: FileTransferBackend

    def _require_enabled(self) -> None:
        if self.plugin.disabled:
            pytest.skip("disabled backend implements raises instead of behavior")

    def test_disabled_is_bool(self) -> None:
        assert isinstance(self.plugin.disabled, bool)

    def test_generate_upload_url_returns_str_containing_key(self) -> None:
        self._require_enabled()
        self.plugin._sign_bucket.sign_url.return_value = (  # noqa: SLF001
            "https://oss.example.com/root/t/tf/f.bin?sig=1"
        )
        url = self.plugin.generate_upload_url("root/t/tf/f.bin", 300)
        assert isinstance(url, str)
        assert "root/t/tf/f.bin" in url

    def test_generate_download_url_preserves_response_params_query(self) -> None:
        self._require_enabled()
        seen: dict = {}

        def _echo(method: str, key: str, expires: int, **_kwargs: object) -> str:
            params = _kwargs.get("params") or {}
            seen.update(params)
            return f"https://oss.example.com/{key}"

        self.plugin._sign_bucket.sign_url = _echo  # noqa: SLF001
        url = self.plugin.generate_download_url(
            "root/t/tf/f.bin",
            300,
            response_params={"response-content-disposition": "attachment"},
        )
        assert seen == {"response-content-disposition": "attachment"}
        assert "root/t/tf/f.bin" in url

    def test_build_staging_path_contract_pattern(self) -> None:
        self._require_enabled()
        path = self.plugin.build_staging_path("tenant-42", "tf-1", "f.bin")
        assert path == "baas-file-transfer/tenant-42/tf-1/f.bin"

    def test_build_session_staging_path_contract_pattern(self) -> None:
        self._require_enabled()
        path = self.plugin.build_session_staging_path(
            "tenant-42", "sess-9", "tf-1", "f.bin"
        )
        assert path == "baas-file-transfer/tenant-42/sess-9/tf-1/f.bin"

    def test_staging_path_components_reject_traversal(self) -> None:
        self._require_enabled()
        with pytest.raises(ValueError, match="Path traversal detected"):
            self.plugin.build_staging_path("..", "tf-1", "f.bin")

    def test_check_object_exists_returns_bool(self) -> None:
        self._require_enabled()
        self.plugin._bucket.head_object.return_value = MagicMock()  # noqa: SLF001
        assert self.plugin.check_object_exists("root/t/tf/f.bin") is True

    def test_multipart_session_shape(self) -> None:
        self._require_enabled()
        self.plugin._bucket.init_multipart_upload.return_value.upload_id = (  # noqa: SLF001
            "upload-1"
        )
        self.plugin._sign_bucket.sign_url.return_value = "https://p/"  # noqa: SLF001
        session = self.plugin.initiate_multipart_upload("root/t/big.bin", 300, 2)
        assert isinstance(session, MultipartSession)
        assert session.session_id == "upload-1"
        assert len(session.parts) == 2
        assert all(isinstance(p, PartInfo) for p in session.parts)


class TestNoopFileTransferBackend(FileTransferBackendContract):
    def setup_method(self) -> None:
        self.plugin = NoopFileTransferBackend()

    def test_disabled_is_true(self) -> None:
        assert self.plugin.disabled is True

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("generate_upload_url", ("k", 300)),
            ("check_object_exists", ("k",)),
            ("generate_download_url", ("k", 300)),
            ("initiate_multipart_upload", ("k", 300)),
            ("list_parts", ("k", "u")),
            ("complete_multipart_upload", ("k", "u", [])),
            ("abort_multipart_upload", ("k", "u")),
            ("delete_object", ("k",)),
            ("build_staging_path", ("t", "tf", "f")),
            ("build_session_staging_path", ("t", "s", "tf", "f")),
        ],
    )
    def test_protocol_methods_raise_disabled_message(
        self, method: str, args: tuple
    ) -> None:
        with pytest.raises(NotImplementedError, match=_DISABLED_MESSAGE):
            getattr(self.plugin, method)(*args)


class TestAliyunOssFileTransferBackend(FileTransferBackendContract):
    def setup_method(self) -> None:
        self._auth_patcher = patch("oss2.Auth")
        self._bucket_patcher = patch("oss2.Bucket")
        self._auth_patcher.start()
        patched_bucket = self._bucket_patcher.start()
        patched_bucket.side_effect = lambda *args, **kwargs: MagicMock()
        config = FileTransferOssConfigSchema(
            endpoint="https://oss.example.com",
            bucket_name="b",
            secret_name="s",
        )
        self.plugin = AliyunOssFileTransferBackend(
            config=config, credentials=("ak", "sk")
        )

    def teardown_method(self) -> None:
        self._bucket_patcher.stop()
        self._auth_patcher.stop()

    def test_disabled_is_false(self) -> None:
        assert self.plugin.disabled is False

    def test_upload_url_signature_method_is_put(self) -> None:
        self.plugin._sign_bucket.sign_url.return_value = "u"  # noqa: SLF001
        self.plugin.generate_upload_url("root/t/tf/f.bin", 300)
        self.plugin._sign_bucket.sign_url.assert_called_once_with(  # noqa: SLF001
            "PUT", "root/t/tf/f.bin", 300
        )

    def test_part_urls_carry_part_number(self) -> None:
        self.plugin._bucket.init_multipart_upload.return_value.upload_id = (  # noqa: SLF001
            "upload-1"
        )
        self.plugin._sign_bucket.sign_url.return_value = "p"  # noqa: SLF001
        self.plugin.initiate_multipart_upload("root/t/big.bin", 300, part_count=2)
        calls = self.plugin._sign_bucket.sign_url.call_args_list  # noqa: SLF001
        assert [c.kwargs["params"]["partNumber"] for c in calls] == ["1", "2"]