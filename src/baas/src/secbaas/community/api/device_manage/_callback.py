"""Callback context models for device-creation async/callback flows.

These are API-layer primitives shared across PaaS adapters, routers, and
SPI consumers (e.g., the TeClaw plugin protocol). Moving them out of the
TeClaw SPI keeps the data model with the other device-manage DTOs and
avoids coupling callback routing to a single provider.
"""

from __future__ import annotations

from pydantic import BaseModel


class DeviceCallbackContext(BaseModel):
    """Context echoed back to BaaS on async/callback completion.

    Carries BaaS-side identifiers (NOT platform-side bot_id) so the callback
    receiver can route the callback to the correct device record. The
    ``operator`` field names the human or service account that initiated the
    device operation, for audit attribution on the callback side.

    Pydantic model: the five fields are required non-empty ``str``s by
    construction, so partial callback contexts fail at the type boundary
    instead of needing a downstream validator.
    """

    model_config = {"frozen": True}

    callback_url: str
    publish_id: str
    device_uuid: str
    tenant: str
    operator: str