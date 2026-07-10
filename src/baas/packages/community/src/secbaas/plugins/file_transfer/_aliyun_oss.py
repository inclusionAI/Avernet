import oss2

from secbaas.logger import get_logger
from secbaas.spi.file_transfer import FileTransferBackend
from secbaas.spi.secret import SecretStorePlugin

log = get_logger("file_transfer")

# Hardcoded per CONTEXT.md D-05 -- no external config source.
OSS_ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
OSS_BUCKET = "secbaas-file-transfer"


class AliyunOssFileTransferBackend(FileTransferBackend):
    """Aliyun OSS implementation of FileTransferBackend.

    Uses oss2 SDK for presigned URL generation and object existence checks.
    AK/SK retrieved from SecretStorePlugin at init time.  Bucket instance
    is created once and reused (singleton pattern via DI container).
    """

    def __init__(self, secret_store: SecretStorePlugin) -> None:
        access_key_id = secret_store.get_secret("secbaas.oss.access_key_id")
        access_key_secret = secret_store.get_secret("secbaas.oss.access_key_secret")
        auth = oss2.Auth(access_key_id, access_key_secret)
        self._bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

    def generate_upload_url(self, staging_path: str, expire_seconds: int) -> str:
        return self._bucket.sign_url("PUT", staging_path, expire_seconds)

    def check_object_exists(self, staging_path: str) -> bool:
        try:
            self._bucket.head_object(staging_path)
            return True
        except oss2.exceptions.NoSuchKey:
            return False

    def generate_download_url(self, staging_path: str, expire_seconds: int) -> str:
        return self._bucket.sign_url("GET", staging_path, expire_seconds)