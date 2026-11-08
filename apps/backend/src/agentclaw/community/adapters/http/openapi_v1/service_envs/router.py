"""Public entry point for isolated service-Bot evaluation deployments."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.exceptions import RequestValidationError

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import accepted, envelope, envelope_errors
from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.publish_flow_service import PublishFlowServiceProtocol
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.kernel.device_dto import ResourceSpecification
from agentclaw.community.di import Injected
from agentclaw.community.utils.env_utils import get_current_env

from .schemas import EvalEnvPublishResult, EvalEnvTeardownResult

router = APIRouter(route_class=PublicAPIRoute, dependencies=[Depends(require_principal)])


@router.post(
    "/openapi/v1/bots/{bot_id}/service-envs/{publication_id}/env-publish",
    status_code=202,
    response_model=Envelope[EvalEnvPublishResult],
)
@envelope_errors
async def publish_eval_env(
    bot_id: BotIdPath,
    publication_id: Annotated[int, Path(ge=1, description="Publication record identifier.")],
    request: Request,
    operator: Annotated[str, Query(min_length=1, description="Caller-supplied business operator; not an authenticated identity.")],
    owner_id: Annotated[str, Query(min_length=1, description="Bot owner locator used for resource lookup; not caller authorization.")],
    biz_id: Annotated[str, Query(description="Business correlation identifier.")] = "",
    service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    publications: BotPublishServiceProtocol = Injected(BotPublishServiceProtocol),
    *,
    cpu: Annotated[int | None, Query(gt=0, description="CPU cores; requires memory.")] = None,
    memory: Annotated[int | None, Query(gt=0, description="Memory in GiB; requires cpu.")] = None,
    disk: Annotated[int | None, Query(gt=0, description="Disk capacity passed to the provider; units and support are provider-defined. Requires cpu and memory.")] = None,
) -> Envelope[EvalEnvPublishResult]:
    """Submit an eval deployment with default TTL; this does not signal readiness.

    ### 调用示例

    将 `BASE_URL` 设置为实际 API 服务入口，并按部署要求附带身份凭据。
    以下 ID 均为占位示例，请替换为实际值；无需 JSON 请求体。

        curl -X POST "${BASE_URL}/openapi/v1/bots/bot-1/service-envs/7/env-publish?owner_id=owner-1&operator=user-1&biz_id=task-1"

    - `bot-1`：源 Bot ID；`7`：Backend 发布记录 ID。
    - `owner_id`、`operator` 必填；`biz_id` 可省略，默认空字符串。
    - 可选资源覆盖示例：在上述 URL 后追加 `&cpu=4&memory=8&disk=200`。
      `cpu`、`memory` 必须同时提供且为正整数；`disk` 可选且为正整数，不能单独传。
      CPU 为核数，内存为 GiB；磁盘值按底层提供方约定透传，不保证单位或支持情况。
      不传资源参数保持现有行为；覆盖仅作用于本次发布，不修改源 Bot 配置。
    - 查询参数含特殊字符时应进行 URL 编码。
    - `operator` 是调用方传入的业务字段，不作为身份凭据。
    - HTTP 202 表示提交成功，不代表环境已就绪。保存响应 `data` 中的
      `baas_publish_id` 用于查询进度，`bot_uuid` 用于释放环境。
    """
    resource_spec = None
    if cpu is not None or memory is not None or disk is not None:
        if cpu is None or memory is None:
            raise RequestValidationError([{
                "type": "value_error", "loc": ("query", "cpu" if cpu is None else "memory"),
                "msg": "cpu and memory must be supplied together for resource overrides",
                "input": None,
            }])
        resource_spec = ResourceSpecification(cpu=cpu, memory=memory, disk=disk)
    _validate_publication_address(bot_id, publication_id, owner_id, bots, publications)
    deployment = await service.eval_publish(
        publish_id=publication_id, operator=operator, biz_id=biz_id, resource_spec=resource_spec,
    )
    return accepted(EvalEnvPublishResult.model_validate(deployment), request)


def _validate_publication_address(
    bot_id: str,
    publication_id: int,
    owner_id: str,
    bots: BotServiceProtocol,
    publications: BotPublishServiceProtocol,
) -> None:
    """Validate that URL identifiers refer to the same Bot resource before use."""
    bot = bots.get_bot_by_address(bot_id=bot_id, user_id=owner_id)
    publication = publications.get_publish_by_id(publication_id)
    if (
        not bot
        or publication is None
        or publication.source_bot_pk != bot["id"]
        or publication.source_bot_id != bot_id
        or publication.env != get_current_env()
    ):
        raise ServicePublicationNotFoundError("publication not found")
    if bot.get("bot_type") != "service":
        raise ServicePublicationUnsupportedError("bot is not a service bot")


@router.get(
    "/openapi/v1/service-envs/env-publish/{baas_publish_id}/progress",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def get_env_publish_progress(
    baas_publish_id: Annotated[int, Path(ge=1, description="BaaS workflow identifier returned by env-publish.")],
    request: Request,
    service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Read workflow progress by BaaS ID without advancing publication or Quality state.

    ### 调用示例

    将 `BASE_URL` 设置为实际 API 服务入口，并按部署要求附带身份凭据。
    用发布响应 `data.baas_publish_id` 的实际值替换示例中的 `42`。

        curl "${BASE_URL}/openapi/v1/service-envs/env-publish/42/progress"

    只需 `baas_publish_id`，无需 Bot ID、owner_id 或请求体。
    HTTP 200 的 `data` 为 BaaS 进度信息；检查 `data.status` 判断结果：
    `SUCCESS` 表示成功，`FAILED` 表示失败，其他状态可能仍在处理中。
    此查询不推进 Quality 任务状态。
    """
    progress = service.get_baas_publish_progress(
        baas_publish_id=baas_publish_id, include_devices=False,
    )
    return envelope(progress, request)


@router.post(
    "/openapi/v1/service-envs/{bot_uuid}/env-teardown",
    status_code=202,
    response_model=Envelope[EvalEnvTeardownResult],
)
@envelope_errors
async def teardown_eval_env(
    bot_uuid: Annotated[str, Path(min_length=1, description="Eval environment bot_uuid returned by env-publish.")],
    request: Request,
    operator: Annotated[str, Query(min_length=1, description="Caller-supplied business operator; not an authenticated identity.")] = "system",
    publish_id: Annotated[int, Query(ge=0, description="Originating publication ID; pass it to share the TTL teardown operation.")] = 0,
    service: PublishFlowServiceProtocol = Injected(PublishFlowServiceProtocol),
) -> Envelope[EvalEnvTeardownResult]:
    """Enqueue environment teardown without advancing Quality task state.

    ### 调用示例

    将 `BASE_URL` 设置为实际 API 服务入口，并按部署要求附带身份凭据。
    用发布响应 `data.bot_uuid` 替换 `eval-bot-1`，用原 Backend 发布记录 ID
    替换 `publish_id=7`；不要将 BaaS 工作流 ID 当作 publish_id。

        curl -X POST "${BASE_URL}/openapi/v1/service-envs/eval-bot-1/env-teardown?operator=user-1&publish_id=7"

    无需请求体。`operator` 默认 `system`；`publish_id` 默认 `0`，建议传入
    原发布记录 ID，使主动释放和 TTL 自动释放使用同一操作记录。
    查询参数含特殊字符时应进行 URL 编码。
    HTTP 202 仅表示释放任务已入队，不代表环境已销毁。
    """
    result = service.eval_teardown(
        bot_uuid, operator=operator, publish_id=publish_id,
    )
    return accepted(EvalEnvTeardownResult.model_validate(result), request)
