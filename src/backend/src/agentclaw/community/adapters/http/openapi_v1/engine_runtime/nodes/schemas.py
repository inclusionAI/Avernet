"""Request/response models for the nodes group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    """A node currently visible to the bot's engine runtime."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "node_id": "node-01",
                "display_name": "Joseph's Mac",
                "platform": "darwin",
                "version": "1.2.0",
                "capabilities": ["screen", "shell"],
                "commands": ["system.run"],
                "remote_ip": "203.0.113.10",
                "status": "online",
            }
        }
    )

    node_id: str = Field(description="Stable node identifier used by runtime calls.")
    display_name: str | None = Field(
        default=None, description="Human-readable node name, when reported."
    )
    platform: str | None = Field(
        default=None, description="Node operating-system or platform name."
    )
    version: str | None = Field(
        default=None, description="Node agent version, when reported."
    )
    capabilities: list[str] = Field(
        default_factory=list, description="Capabilities declared by the node."
    )
    commands: list[str] = Field(
        default_factory=list, description="Commands declared by the node."
    )
    remote_ip: str | None = Field(
        default=None, description="Remote address reported by the engine."
    )
    status: str = Field(description="Current node status reported by the engine.")


__all__ = ["Node"]
