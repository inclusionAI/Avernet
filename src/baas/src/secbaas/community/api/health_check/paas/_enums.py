"""
PaaS 平台类型枚举

用于区分实际的 PaaS 平台：
- ARCA: Arca 沙箱平台
- POOLAB: Poolab 平台
- SIGMA: Sigma 平台（预留）
- LOCAL: 本地开发环境
- TECLAW: TeClaw 平台
- DOCKER: Docker 独立平台

用于健康检查策略路由。
"""

from enum import StrEnum


class PaaSProviderType(StrEnum):
    ARCA = "ARCA"
    POOLAB = "POOLAB"
    SIGMA = "SIGMA"
    LOCAL = "local"
    TECLAW = "TECLAW"
    K8S = "K8S"  # [D-01] Kubernetes PaaS platform type
    DOCKER = "DOCKER"  # Docker 独立平台
