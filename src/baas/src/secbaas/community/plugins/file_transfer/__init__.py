"""FileTransferBackend plugin implementations."""

# Import order inside this block is non-alphabetical on purpose (I001):
# _aliyun_oss imports secbaas.community.bootstrap._configs, which
# transitively imports this package via bootstrap/plugins/_plugin_core.py.
# The proxy, the no-op backend and the projector must already be bound
# here, so _aliyun_oss has to go last — otherwise the re-entrant package
# import at bootstrap time raises a circular-import ImportError.
from ._http_proxy import OssStreamingProxy  # noqa: I001
from ._noop import NoopFileTransferBackend, NoopSessionFileUrlProjector
from .aliyun_ack import AliyunAckSessionFileUrlProjector
from ._aliyun_oss import AliyunOssFileTransferBackend

__all__ = [
    "AliyunAckSessionFileUrlProjector",
    "AliyunOssFileTransferBackend",
    "NoopFileTransferBackend",
    "NoopSessionFileUrlProjector",
    "OssStreamingProxy",
]
