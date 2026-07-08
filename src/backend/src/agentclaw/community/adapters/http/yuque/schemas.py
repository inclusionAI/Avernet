from pydantic import BaseModel, Field


class YuqueVerifyRequest(BaseModel):
    url: str = Field(..., description="语雀 URL，例如 https://yuque.example.com/aixcoding/tech")
    team_token: str = Field(..., description="语雀 teamToken，作为 X-Auth-Token 请求头透传")


class YuqueVerifyData(BaseModel):
    bound: bool = Field(..., description="是否绑定成功：login 与 URL 第一层路径相等")
    login: str = Field("", description="语雀用户 login")
    namespace: str = Field("", description="URL 第一层路径")


class YuqueVerifyResponse(BaseModel):
    success: bool
    data: YuqueVerifyData | None = None
    error: str | None = None
