"""API Key 验证模块

高频操作，只读，可扩展缓存/限流
"""

from secbaas.api.api_gateway import APIKeyRecord
from secbaas.core.repository.api_gateway import APIKeyRepository
from secbaas.core.utils import env_utils
from secbaas.logger import get_logger

from ._key_gen import APIKeyGenerator
from ._protocols import APIKeyValidator

logger = get_logger("core-service")


class DefaultAPIKeyValidator(APIKeyValidator):
    """API Key 验证器默认实现"""

    def __init__(self, repository: APIKeyRepository):
        self._repository = repository

    async def verify(self, api_key: str) -> APIKeyRecord | None:
        """验证 API Key 有效性

        Args:
            api_key: 完整的 API Key

        Returns:
            验证通过返回 APIKeyRecord，失败返回 None
        """
        if not api_key or len(api_key) < 8:
            logger.warning("[verify] Invalid api_key format")
            return None

        prefix = api_key[:8]

        # 鉴权钉死当前环境：共享 DB 下避免跨环境 API Key 互认。
        # env 口径与创建侧一致（均经 env_utils.get_current_env 归一）。
        record = self._repository.get_by_prefix_and_status(
            prefix, "ACTIVE", env=env_utils.get_current_env()
        )

        if record is None:
            logger.debug(f"[verify] No active key found for prefix: {prefix}")
            return None

        if not APIKeyGenerator.verify_key(api_key, record.api_key_hash):
            logger.warning(f"[verify] Hash verification failed for prefix: {prefix}")
            return None

        logger.debug(
            f"[verify] API Key verified: id={record.id}, app_id={record.app_id}"
        )
        return record

    def verify_sync(self, api_key: str) -> APIKeyRecord | None:
        """同步验证 API Key 有效性（用于非异步场景）

        Args:
            api_key: 完整的 API Key

        Returns:
            验证通过返回 APIKeyRecord，失败返回 None
        """
        if not api_key or len(api_key) < 8:
            logger.warning("[verify_sync] Invalid api_key format")
            return None

        prefix = api_key[:8]

        record = self._repository.get_by_prefix_and_status(
            prefix, "ACTIVE", env=env_utils.get_current_env()
        )

        if record is None:
            logger.debug(f"[verify_sync] No active key found for prefix: {prefix}")
            return None

        if not APIKeyGenerator.verify_key(api_key, record.api_key_hash):
            logger.warning(
                f"[verify_sync] Hash verification failed for prefix: {prefix}"
            )
            return None

        logger.debug(
            f"[verify_sync] API Key verified: id={record.id}, app_id={record.app_id}"
        )
        return record
