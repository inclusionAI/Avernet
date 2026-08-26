"""Public contracts for service-Bot container instances."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import Path
from pydantic import BaseModel, Field


ContainerStatus = Literal["healthy", "restarting", "abnormal", "unknown"]

InstanceIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        description="Stable instance identifier returned by the container list.",
    ),
]


class ContainerSummary(BaseModel):
    """Counts derived from the current BaaS instance snapshot."""

    total: int = Field(description="Total live instances in the snapshot.")
    healthy: int = Field(description="Instances whose health probe is healthy.")
    abnormal: int = Field(description="Instances whose health probe is unhealthy.")
    restarting: int = Field(description="Instances currently pending or updating.")
    unknown: int = Field(description="Instances without a usable health result.")


class ContainerInstance(BaseModel):
    """One runtime instance backing a service Bot."""

    id: str = Field(description="Stable instance identifier used by instance actions.")
    node: str | None = Field(
        default=None,
        description="Runtime node name; null until BaaS exposes this metric.",
    )
    cpu: str | None = Field(
        default=None,
        description="CPU usage or allocation; null until BaaS exposes metrics.",
    )
    memory: str | None = Field(
        default=None,
        description="Memory usage or allocation; null until BaaS exposes metrics.",
    )
    status: ContainerStatus = Field(description="Stable product health state.")
    internal_status: str | None = Field(
        default=None, description="Raw BaaS lifecycle state for diagnostics."
    )
    engine: str = Field(description="Engine running in this instance.")
    provider: str | None = Field(default=None, description="Runtime provider type.")
    provider_instance_id: str | None = Field(
        default=None, description="Provider-side instance identifier."
    )
    created_at: datetime | None = Field(
        default=None, description="Instance creation timestamp."
    )


class ContainerList(BaseModel):
    """Current service-Bot instance snapshot and derived summary."""

    bot_id: str = Field(description="Stable Bot identifier.")
    summary: ContainerSummary = Field(
        description="Counts derived from the same instance snapshot."
    )
    instances: list[ContainerInstance] = Field(
        description="Current runtime instances backing the service Bot."
    )


class ContainerRestart(BaseModel):
    """Acknowledgement for an asynchronous single-instance restart."""

    bot_id: str = Field(description="Stable Bot identifier.")
    instance_id: str = Field(description="Restarted instance identifier.")
    publish_id: int | None = Field(
        default=None, description="BaaS publish workflow identifier."
    )
    accepted: bool = Field(description="Whether the restart was accepted.")
