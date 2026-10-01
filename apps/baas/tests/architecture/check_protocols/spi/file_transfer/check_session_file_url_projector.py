from secbaas.community.plugins.file_transfer import (
    AliyunAckSessionFileUrlProjector,
    NoopSessionFileUrlProjector,
)
from secbaas.community.spi.file_transfer import (
    SessionFileUrlProjector as SessionFileUrlProjectorProtocol,
)

# Assign value, will trigger mypy type check
_noop_projector: SessionFileUrlProjectorProtocol = NoopSessionFileUrlProjector()
_aliyun_projector: SessionFileUrlProjectorProtocol = AliyunAckSessionFileUrlProjector(
    proxy_base_url="https://bff.example.com", deploy_tenant="aliyun"
)
