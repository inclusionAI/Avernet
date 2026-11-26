"""Task claim Bot authorization request and response schemas."""

from pydantic import BaseModel, ConfigDict, Field


# ===== 任务认领 Bot 授权(grant/revoke)DTO =====
# 对齐 api-contract §1:`bcs_bot_id` = real:entity(bot_id:owner_user_id);
# cookie/referer 取自请求头(schema 内不承载,router 注入),operator = 登录态用户(staffId)。
# stateless:api-key 服务端持有,不落本地表;/grant、/revoke 端点对外在 openapi v1 task router。


class TaskGrantRequestDTO(BaseModel):
    """前端开启「任务认领」grant 公共 api-key 给某 Bot 的请求体。"""

    model_config = ConfigDict(extra="ignore")

    bcs_bot_id: str = Field(
        ...,
        description="被授权 Bot的 real:entity(bot_id:owner_user_id,即 /mine 的 bot.id 原值);"
        "遗留无 ':' 由后端用登录态 operator 补 owner 段",
    )


class TaskGrantResultDTO(BaseModel):
    """grant 成功回包。"""

    bcs_bot_id: str = Field(..., description="被授权 Bot 的 real:entity")
    api_key_prefix: str = Field(..., description="授权所用 api-key 前缀(secbaas 主键)")
    grant_status: str = Field(..., description="授权状态(granted)")
    operator: str = Field(..., description="执行 grant 的用户 id")


class TaskRevokeRequestDTO(BaseModel):
    """关闭「任务认领」撤销授权的请求体。"""

    model_config = ConfigDict(extra="ignore")

    bcs_bot_id: str = Field(..., description="撤销授权 Bot 的 real:entity")


class TaskRevokeResultDTO(BaseModel):
    """revoke 成功回包。"""

    bcs_bot_id: str = Field(..., description="撤销授权 Bot 的 real:entity")
    grant_status: str = Field(..., description="授权状态(revoked)")
