"""Auth SPI — shared data models.

Implementation-agnostic AuthUser used as the return type for
AuthPlugin.get_login_user().
"""

from pydantic import BaseModel, ConfigDict, Field


class AuthUser(BaseModel):
    """Authenticated user information.

    Used as the return type for AuthPlugin.get_login_user().
    Both bare and enterprise implementations use this model.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    mobileNumber: str | None = None  # noqa: N815 -- matches API field name
    nickName: str | None = None  # noqa: N815 -- matches API field name (花名)
    operatorName: str  # noqa: N815 -- matches API field name (主域帐号)
    outUserNo: str = Field(alias="staffId")  # noqa: N815 -- matches API field name (工号)
    realName: str | None = None  # noqa: N815 -- matches API field name
    tntInstId: str | None = Field(default=None, alias="tenantId")  # noqa: N815

    @property
    def tenantId(self) -> str | None:  # noqa: N802 -- property alias for tntInstId
        return self.tntInstId

    @property
    def staffId(self) -> str:  # noqa: N802 -- property alias for outUserNo
        return self.outUserNo
