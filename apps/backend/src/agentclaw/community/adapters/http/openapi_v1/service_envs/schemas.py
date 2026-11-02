"""Public schemas for service-Bot evaluation environments."""

from typing import Literal

from pydantic import BaseModel, Field


class EvalEnvPublishResult(BaseModel):
    """Submitted eval deployment; success does not imply runtime readiness."""

    success: bool = Field(description="Whether the operation was accepted.")
    publish_id: int = Field(description="Source Backend publication identifier.")
    stage: Literal["eval"] = Field(description="Deployment stage; always eval.")
    bot_uuid: str = Field(description="BaaS Bot UUID for the isolated evaluation environment.")
    baas_publish_id: int = Field(description="BaaS workflow identifier used to query deployment progress.")
    baas_bot_status: str | None = Field(default=None, description="BaaS Bot status when submission completed, if available.")


class EvalEnvTeardownResult(BaseModel):
    """Acknowledgement that environment teardown was enqueued."""

    success: bool = Field(description="Whether the operation was accepted.")
    bot_uuid: str = Field(description="BaaS Bot UUID for the isolated evaluation environment.")
    default_tag: str = Field(description="Tag used to identify the evaluation environment.")
    message: str = Field(description="Teardown submission result message.")
