"""Only product inputs; actor, tenant and ECB origin are never user inputs."""

from typing import Literal

from agentclaw.community.adapters.http.knowledge_management.schemas import StrictBody
from pydantic import Field, model_validator


class YuqueItem(StrictBody):
    doc_id: int | None = Field(None, gt=0)
    slug: str | None = Field(None, max_length=512)
    namespace: str | None = Field(None, max_length=512)
    source_url: str | None = Field(None, max_length=2048)
    title: str | None = Field(None, max_length=512)

    @model_validator(mode="after")
    def locator(self):
        if not (self.doc_id or self.source_url or (self.namespace and self.slug)):
            raise ValueError("document locator is required")
        return self


class YuqueImport(StrictBody):
    items: list[YuqueItem] = Field(min_length=1, max_length=20)


class Calendar(StrictBody):
    frequency: Literal["daily", "weekly"]
    time: str = Field("", max_length=5)
    timezone: str = Field("Asia/Shanghai", max_length=64)
    weekdays: list[int] = Field(default_factory=list, max_length=7)


class AutoSync(StrictBody):
    enabled: bool
    expected_version: int = Field(ge=0)
    schedule: Calendar | None = None
