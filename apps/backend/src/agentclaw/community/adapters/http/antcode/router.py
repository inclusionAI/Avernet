"""Router for AntCode API integration.

API layer: thin HTTP adapter that injects core AntCodeService.
All business logic lives in core/antcode/services/.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.antcode.schemas import ProjectsListResponse, ErrorResponse
from agentclaw.community.adapters.http.dependencies import get_request_context, RequestContext
from agentclaw.community.api.code_platform_service import CodePlatformServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()
router = APIRouter(prefix="/api/antcode", tags=["antcode"])


@router.get(
    "/projects",
    response_model=ProjectsListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def search_user_projects(
    request: Request,
    q: Optional[str] = Query(
        None,
        description="Search keyword for project name or path",
        min_length=1,
        max_length=100
    ),
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of projects per page"),
    ctx: RequestContext = Depends(get_request_context),
    antcode_service: CodePlatformServiceProtocol = Injected(CodePlatformServiceProtocol),
) -> ProjectsListResponse:
    """Search projects that the current user has access to.

    This endpoint proxies the request to AntCode API and returns projects
    based on the user's permissions and search criteria.

    Examples:
        - Search projects by keyword:
          GET /api/antcode/projects?q=myproject

        - Get all projects with pagination:
          GET /api/antcode/projects?page=2&per_page=50
    """
    # Get user cookie from request
    cookie = "; ".join([f"{k}={v}" for k, v in request.cookies.items()])

    if not cookie:
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error_code": "AUTH_FAILED",
                "error_message": "User not authenticated. Please login first."
            }
        )

    # Call AntCode service with cookie
    result = antcode_service.search_user_projects(
        cookie=cookie,
        search=q,
        page=page,
        per_page=per_page
    )

    # Check if request was successful
    if not result.get("success"):
        error_code = result.get("error_code", "UNKNOWN_ERROR")
        error_message = result.get("error_message", "Unknown error occurred")

        # Map error codes to HTTP status codes
        status_code = 500
        if error_code == "AUTH_FAILED":
            status_code = 401
        elif error_code == "API_ERROR":
            status_code = 502  # Bad Gateway (upstream API error)

        logger.error(f"AntCode API call failed: {error_code} - {error_message}")
        raise HTTPException(status_code=status_code, detail=result)

    # Return successful response
    return ProjectsListResponse(**result)
