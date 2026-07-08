"""
API Key 管理服务层

封装 API Key 相关的业务逻辑
"""

from secbaas.api import OperationContext
from secbaas.api.api_gateway import (
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyError,
    APIKeyListResponse,
    APIKeyQuery,
    APIKeyRecord,
    APIKeyResponse,
    APIKeyService,
    APIKeyStatus,
    APIKeyUpdate,
)
from secbaas.core.repository.api_gateway import APIKeyRepository
from secbaas.core.service.api_gateway import APIKeyGenerator
from secbaas.logger import get_logger

logger = get_logger("core-service")


def _record_to_response(record: APIKeyRecord) -> APIKeyResponse:
    """将 APIKeyRecord 转换为 APIKeyResponse"""
    return APIKeyResponse(
        id=record.id,
        app_id=record.app_id,
        app_type=record.app_type,
        key_name=record.key_name,
        api_key_prefix=record.api_key_prefix,
        description=record.description,
        rate_limit_rpm=record.rate_limit_rpm,
        rate_limit_rpd=record.rate_limit_rpd,
        status=record.status,
        owner=record.owner,
        tenant=record.tenant,
        env=record.env,
        creator=record.creator,
        modifier=record.modifier,
        policy=record.policy,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


class DefaultAPIKeyService(APIKeyService):
    """API Key 管理服务"""

    def __init__(self, repository: APIKeyRepository):
        self._repository = repository

    async def create_key(
        self, data: APIKeyCreate, ctx: OperationContext
    ) -> APIKeyCreateResponse:
        """创建 API Key

        Args:
             data: 创建请求数据
             ctx: 操作上下文

        Returns:
            创建的 API Key（包含明文密钥，仅此一次）
        """
        logger.info(f"Creating API Key: app_id={data.app_id}, operator={ctx.operator}")

        # 1. 生成 API Key（带前缀碰撞重试）
        max_retries = 3
        for attempt in range(max_retries):
            api_key = APIKeyGenerator.generate()
            api_key_hash = APIKeyGenerator.hash_key(api_key)
            api_key_prefix = api_key[:8]

            # 2. 检查前缀是否已存在
            if not self._repository.exists_prefix(api_key_prefix):
                break
            logger.warning(
                f"Prefix collision: {api_key_prefix}, retry {attempt + 1}/{max_retries}"
            )
        else:
            raise APIKeyError(
                code=500001,
                message="无法生成唯一的 API Key 前缀，请重试",
            )

        # 3. 存储
        key_id = self._repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name=data.key_name,
            app_id=data.app_id,
            app_type=data.app_type,
            description=data.description,
            rate_limit_rpm=data.rate_limit_rpm,
            rate_limit_rpd=data.rate_limit_rpd,
            status=APIKeyStatus.ACTIVE.value,
            owner=data.owner or ctx.operator,
            tenant=data.tenant,
            env=ctx.env,
            creator=ctx.operator,
            policy=data.policy,
        )

        logger.info(f"API Key created: id={key_id}, app_id={data.app_id}")

        # 4. 查询并返回（包含明文密钥）
        record = self._repository.get_by_id(key_id)
        assert record is not None, f"APIKey record not found after insert: id={key_id}"
        response = _record_to_response(record)

        return APIKeyCreateResponse(**response.model_dump(), api_key=api_key)

    async def get_key(
        self, key_id: int, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """查询单个 API Key

        Args:
            key_id: API Key ID
            ctx: 操作上下文

        Returns:
            API Key 信息或 None
        """
        logger.info(f"Getting API Key: id={key_id}, operator={ctx.operator}")

        record = self._repository.get_by_id(key_id)

        if record is None:
            return None

        return _record_to_response(record)

    async def get_key_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """根据前缀查询 API Key

        Args:
            prefix: API Key 前缀
            ctx: 操作上下文

        Returns:
            API Key 信息或 None
        """
        logger.info(
            f"Getting API Key by prefix: prefix={prefix}, operator={ctx.operator}"
        )

        record = self._repository.get_by_prefix(prefix)

        if record is None:
            return None

        return _record_to_response(record)

    async def list_keys(
        self,
        query: APIKeyQuery,
        ctx: OperationContext,
        page: int = 1,
        page_size: int = 20,
    ) -> APIKeyListResponse:
        """查询 API Key 列表

        Args:
            query: 查询条件
            ctx: 操作上下文
            page: 页码
            page_size: 每页数量

        Returns:
            API Key 列表
        """
        logger.info(
            f"Listing API Keys: app_id={query.app_id}, app_type={query.app_type}, "
            f"status={query.status}, creator={query.creator}, owner={query.owner}, "
            f"tenant={query.tenant}, env={ctx.env}, page={page}, operator={ctx.operator}"
        )

        status = query.status.value if query.status else None

        total, records = self._repository.list_keys(
            app_id=query.app_id,
            app_type=query.app_type,
            status=status,
            creator=query.creator,
            owner=query.owner,
            tenant=query.tenant,
            env=ctx.env,
            page=page,
            page_size=page_size,
        )

        items = [_record_to_response(r) for r in records]
        return APIKeyListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update_key(
        self, key_id: int, data: APIKeyUpdate, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """更新 API Key 元数据

        Args:
            key_id: API Key ID
            data: 更新数据
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息或 None
        """
        logger.info(f"Updating API Key: id={key_id}, operator={ctx.operator}")

        record = self._repository.get_by_id(key_id)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        self._repository.update(
            key_id,
            key_name=data.key_name,
            description=data.description,
            app_id=data.app_id,
            app_type=data.app_type,
            rate_limit_rpm=data.rate_limit_rpm,
            rate_limit_rpd=data.rate_limit_rpd,
            owner=data.owner,
            tenant=data.tenant,
            policy=data.policy,
            modifier=ctx.operator,
        )

        logger.info(f"API Key updated: id={key_id}")

        record = self._repository.get_by_id(key_id)
        assert record is not None, f"APIKey record not found after update: id={key_id}"
        return _record_to_response(record)

    async def update_key_by_prefix(
        self, prefix: str, data: APIKeyUpdate, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """根据前缀更新 API Key 元数据

        Args:
            prefix: API Key 前缀
            data: 更新数据
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息或 None
        """
        logger.info(
            f"Updating API Key by prefix: prefix={prefix}, operator={ctx.operator}"
        )

        record = self._repository.get_by_prefix(prefix)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        self._repository.update(
            record.id,
            key_name=data.key_name,
            description=data.description,
            app_id=data.app_id,
            app_type=data.app_type,
            rate_limit_rpm=data.rate_limit_rpm,
            rate_limit_rpd=data.rate_limit_rpd,
            owner=data.owner,
            tenant=data.tenant,
            policy=data.policy,
            modifier=ctx.operator,
        )

        logger.info(f"API Key updated: prefix={prefix}")

        record = self._repository.get_by_id(record.id)
        assert record is not None, (
            f"APIKey record not found after update: prefix={prefix}"
        )
        return _record_to_response(record)

    async def activate(
        self, key_id: int, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """启用 API Key

        Args:
            key_id: API Key ID
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息

        Raises:
            APIKeyError: 状态不允许此操作
        """
        logger.info(f"Activating API Key: id={key_id}, operator={ctx.operator}")

        record = self._repository.get_by_id(key_id)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        if record.status != APIKeyStatus.INACTIVE.value:
            raise APIKeyError(
                code=400001,
                message=f"只有 INACTIVE 状态的 Key 可以启用，当前状态: {record.status}",
            )

        self._repository.update_status(key_id, APIKeyStatus.ACTIVE.value, ctx.operator)

        logger.info(f"API Key activated: id={key_id}")

        record = self._repository.get_by_id(key_id)
        assert record is not None, (
            f"APIKey record not found after activate: id={key_id}"
        )
        return _record_to_response(record)

    async def activate_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """根据前缀启用 API Key

        Args:
            prefix: API Key 前缀
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息

        Raises:
            APIKeyError: 状态不允许此操作
        """
        logger.info(
            f"Activating API Key by prefix: prefix={prefix}, operator={ctx.operator}"
        )

        record = self._repository.get_by_prefix(prefix)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        if record.status != APIKeyStatus.INACTIVE.value:
            raise APIKeyError(
                code=400001,
                message=f"只有 INACTIVE 状态的 Key 可以启用，当前状态: {record.status}",
            )

        self._repository.update_status(
            record.id, APIKeyStatus.ACTIVE.value, ctx.operator
        )

        logger.info(f"API Key activated: prefix={prefix}")

        record = self._repository.get_by_id(record.id)
        assert record is not None, (
            f"APIKey record not found after activate: prefix={prefix}"
        )
        return _record_to_response(record)

    async def deactivate(
        self,
        key_id: int,
        ctx: OperationContext,
    ) -> APIKeyResponse | None:
        """停用 API Key

        Args:
            key_id: API Key ID
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息

        Raises:
            APIKeyError: 状态不允许此操作
        """
        logger.info(f"Deactivating API Key: id={key_id}, operator={ctx.operator}")

        record = self._repository.get_by_id(key_id)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        if record.status != APIKeyStatus.ACTIVE.value:
            raise APIKeyError(
                code=400002,
                message=f"只有 ACTIVE 状态的 Key 可以停用，当前状态: {record.status}",
            )

        self._repository.update_status(
            key_id, APIKeyStatus.INACTIVE.value, ctx.operator
        )

        logger.info(f"API Key deactivated: id={key_id}")

        record = self._repository.get_by_id(key_id)
        assert record is not None, (
            f"APIKey record not found after deactivate: id={key_id}"
        )
        return _record_to_response(record)

    async def deactivate_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """根据前缀停用 API Key

        Args:
            prefix: API Key 前缀
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息

        Raises:
            APIKeyError: 状态不允许此操作
        """
        logger.info(
            f"Deactivating API Key by prefix: prefix={prefix}, operator={ctx.operator}"
        )

        record = self._repository.get_by_prefix(prefix)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        if record.status != APIKeyStatus.ACTIVE.value:
            raise APIKeyError(
                code=400002,
                message=f"只有 ACTIVE 状态的 Key 可以停用，当前状态: {record.status}",
            )

        self._repository.update_status(
            record.id, APIKeyStatus.INACTIVE.value, ctx.operator
        )

        logger.info(f"API Key deactivated: prefix={prefix}")

        record = self._repository.get_by_id(record.id)
        assert record is not None, (
            f"APIKey record not found after deactivate: prefix={prefix}"
        )
        return _record_to_response(record)

    async def revoke(self, key_id: int, ctx: OperationContext) -> APIKeyResponse | None:
        """吊销 API Key

        Args:
            key_id: API Key ID
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息

        Raises:
            APIKeyError: 状态不允许此操作
        """
        logger.info(f"Revoking API Key: id={key_id}, operator={ctx.operator}")

        record = self._repository.get_by_id(key_id)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        if record.status == APIKeyStatus.REVOKED.value:
            raise APIKeyError(code=400003, message="API Key 已吊销")

        self._repository.update_status(key_id, APIKeyStatus.REVOKED.value, ctx.operator)

        logger.info(f"API Key revoked: id={key_id}")

        record = self._repository.get_by_id(key_id)
        assert record is not None, f"APIKey record not found after revoke: id={key_id}"
        return _record_to_response(record)

    async def revoke_by_prefix(
        self, prefix: str, ctx: OperationContext
    ) -> APIKeyResponse | None:
        """根据前缀吊销 API Key

        Args:
            prefix: API Key 前缀
            ctx: 操作上下文

        Returns:
            更新后的 API Key 信息

        Raises:
            APIKeyError: 状态不允许此操作
        """
        logger.info(
            f"Revoking API Key by prefix: prefix={prefix}, operator={ctx.operator}"
        )

        record = self._repository.get_by_prefix(prefix)

        if record is None:
            return None

        if record.env != ctx.env:
            raise APIKeyError(
                code=400004,
                message=f"环境不匹配，Key 所在环境: {record.env}，当前环境: {ctx.env}",
            )

        if record.status == APIKeyStatus.REVOKED.value:
            raise APIKeyError(code=400003, message="API Key 已吊销")

        self._repository.update_status(
            record.id, APIKeyStatus.REVOKED.value, ctx.operator
        )

        logger.info(f"API Key revoked: prefix={prefix}")

        record = self._repository.get_by_id(record.id)
        assert record is not None, (
            f"APIKey record not found after revoke: prefix={prefix}"
        )
        return _record_to_response(record)
