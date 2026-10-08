"""Authenticated OPEN catalog, with the employee platform's documented payload."""
import asyncio
from typing import Generic, TypeVar

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.api.digital_employee_service import DigitalEmployeeCatalogProtocol, DigitalEmployeeServiceProtocol
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError
from agentclaw.community.di import Injected

T = TypeVar("T")


class AgentSummary(BaseModel):
    """A service Bot available for digital employee registration."""
    agentId: str = Field(description="Stable Bot metadata identifier.", examples=["17"])
    platform: str = Field(description="Registered source platform code.", examples=["example-platform"])
    agentName: str | None = Field(default=None, description="Bot display name.", examples=["Support Bot"])
    description: str | None = Field(default=None, description="Bot description.", examples=["Answers support questions."])
    creatorNo: str = Field(description="Bot creator identifier.", examples=["creator-17"])
    aiWorkNo: str | None = Field(default=None, description="Bound digital employee identifier, when present.", examples=["AI00000124"])


class McpDetail(BaseModel):
    """A configured remote MCP capability."""
    mcp_id: str = Field(description="MCP server code.", examples=["support-tools"])
    name: str = Field(description="MCP display name.", examples=["Support tools"])
    description: str | None = Field(default=None, description="MCP description.", examples=["Search support records."])


class SkillDetail(BaseModel):
    """One exact Skill package supplied for registration security scanning."""
    skill_id: str = Field(description="Skill identifier.", examples=["9"])
    name: str = Field(description="Skill runtime name.", examples=["support-skill"])
    skill_source: str = Field(description="Skill source classification.", examples=["custom"])
    version: str = Field(description="Exact upstream version or package content digest.", examples=["1.0.0"])
    bundleUrl: str = Field(description="Temporary signed URL for the complete scan package.", examples=["https://objects.example.test/skill.zip"])
    description: str | None = Field(default=None, description="Skill description.", examples=["Handles support questions."])
    owner: str | None = Field(default=None, description="Skill owner identifier, when known.", examples=["owner-17"])
    modifier: str | None = Field(default=None, description="Skill modifier identifier, when known.", examples=["owner-17"])


class CliDetail(BaseModel):
    """An authorized CLI capability, identified by its grant code."""
    cliCode: str = Field(description="CLI capability code.", examples=["support-cli"])
    name: str = Field(description="CLI display name.", examples=["Support CLI"])
    description: str | None = Field(default=None, description="CLI description.", examples=["Queries support records."])


class AgentDetail(AgentSummary):
    """Service Bot metadata and the capabilities of the selected version."""
    agentName: str = Field(description="Bot display name.", examples=["Support Bot"])
    agentCode: str = Field(description="AgentPass agent code.", examples=["agent-17"])
    agentPlatform: str = Field(description="Registered source platform code.", examples=["example-platform"])
    agentFramework: str = Field(description="Bot engine type.", examples=["openclaw"])
    modelList: list[str] = Field(description="Models available from a runnable Bot instance.", examples=[["provider/model"]])
    mcpDetailList: list[McpDetail] = Field(description="Remote MCP capabilities of this version.")
    skillList: list[SkillDetail] = Field(description="Complete active Skill packages of this version.")
    cliLis: list[CliDetail] = Field(description="Shared AgentPass CLI capabilities. The field spelling follows the platform contract.")
    queriedVersionStatus: str = Field(description="Selected configuration version: draft, published, or applying.", examples=["draft"])


class PlatformResponse(BaseModel, Generic[T]):
    """Digital employee platform response; HTTP authentication errors use the standard OpenAPI error envelope."""
    model_config = ConfigDict(json_schema_extra={"description": "Digital employee platform catalog response."})
    data: T = Field(description="Requested catalog data.")
    success: bool = Field(default=True, description="Whether the business operation succeeded.", examples=[True])
    errorMessage: str | None = Field(default=None, description="Business failure description.", examples=["Integration configuration is incomplete."])
    errorCode: str | None = Field(default=None, description="Business failure code.", examples=["DIGITAL_EMPLOYEE_UNAVAILABLE"])
    traceId: str = Field(description="Request trace identifier.", examples=["request-17"])


router = APIRouter(prefix="/openapi/v1/bots/metadata/digital-employees", tags=["digital-employees"], route_class=PublicAPIRoute, dependencies=[Depends(require_principal)])


@router.get("", response_model=PlatformResponse[list[AgentSummary]])
@envelope_errors
async def list_agents(
    request: Request,
    creatorNo: str = Query(min_length=1, max_length=128, pattern=r".*\S.*", description="Creator identifier supplied by the authenticated registration platform."),
    service: DigitalEmployeeServiceProtocol = Injected(DigitalEmployeeServiceProtocol),
):
    try:
        data = await asyncio.to_thread(service.list_bindable, creatorNo)
        return PlatformResponse(data=data, traceId=envelope(None, request).request_id)
    except DigitalEmployeeError as exc:
        return PlatformResponse(data=[], success=False, errorCode="DIGITAL_EMPLOYEE_UNAVAILABLE",
                                errorMessage=str(exc), traceId=envelope(None, request).request_id)


@router.get("/{agent_id}", response_model=PlatformResponse[AgentDetail | None])
@envelope_errors
async def get_agent(
    request: Request,
    agent_id: int = Path(gt=0, description="Stable metadata identifier returned by the catalog."),
    versionStatus: str = Query(default="draft", pattern="^(draft|published|applying)$", description="Configuration version to inspect."),
    service: DigitalEmployeeCatalogProtocol = Injected(DigitalEmployeeCatalogProtocol),
):
    try:
        data = await service.detail(agent_id, versionStatus)
        return PlatformResponse(data=data, traceId=envelope(None, request).request_id)
    except DigitalEmployeeError as exc:
        return PlatformResponse(data=None, success=False, errorCode="DIGITAL_EMPLOYEE_UNAVAILABLE",
                                errorMessage=str(exc), traceId=envelope(None, request).request_id)
