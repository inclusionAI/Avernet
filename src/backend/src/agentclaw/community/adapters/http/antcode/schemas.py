"""Data models for AntCode API integration."""

from typing import Optional, List
from pydantic import BaseModel, Field


class Namespace(BaseModel):
    """Project namespace information."""
    id: int = Field(..., description="Namespace ID")
    name: str = Field(..., description="Namespace name")
    path: str = Field(..., description="Namespace path")
    kind: Optional[str] = Field(None, description="Namespace kind: 'user' or 'group'")


class ProjectPermissions(BaseModel):
    """Project permissions information."""
    project_access: Optional[dict] = Field(None, description="Project access level")


class Project(BaseModel):
    """AntCode/GitLab project information."""
    id: int = Field(..., description="Project ID")
    name: str = Field(..., description="Project name")
    path: str = Field(..., description="Project path")
    web_url: str = Field(..., description="Project web URL")
    description: Optional[str] = Field(None, description="Project description")
    visibility: str = Field(..., description="Project visibility: public, internal, private")
    last_activity_at: str = Field(..., description="Last activity timestamp")
    namespace: Namespace = Field(..., description="Project namespace")
    permissions: Optional[ProjectPermissions] = Field(None, description="User permissions in project")


class ProjectsListResponse(BaseModel):
    """Response for project list query."""
    success: bool = Field(..., description="Request success status")
    total: int = Field(..., description="Total number of projects")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Projects per page")
    total_pages: int = Field(..., description="Total number of pages")
    projects: List[Project] = Field(..., description="List of projects")


class ErrorResponse(BaseModel):
    """Error response model."""
    success: bool = Field(False, description="Request success status")
    error_code: str = Field(..., description="Error code")
    error_message: str = Field(..., description="Error message")
    details: Optional[dict] = Field(None, description="Additional error details")
