from secbaas.community.bootstrap._configs import FileTransferOssConfigSchema
from secbaas.community.plugins.file_transfer import (
    AliyunOssFileTransferBackend,
    NoopFileTransferBackend,
)
from secbaas.community.spi.file_transfer import FileTransferBackend

# Assign value, will trigger mypy type check
_noop_ft: FileTransferBackend = NoopFileTransferBackend()
_aliyun_ft: FileTransferBackend = AliyunOssFileTransferBackend(
    config=FileTransferOssConfigSchema(endpoint="e", bucket_name="b"),
    credentials=("k", "s"),
)
