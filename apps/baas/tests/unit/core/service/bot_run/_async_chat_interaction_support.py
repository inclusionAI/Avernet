"""async chat interaction support."""

from unittest.mock import MagicMock

from secbaas.community.api.bot_interaction import (
    InteractionResolution,
)
from secbaas.community.core.service.bot_interaction import InteractionDispatch
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)


def _make_exchange(request_id="e-1", accepted=True) -> EngineInteractionResolveExchange:
    request = {
        "type": "req",
        "id": request_id,
        "method": "interaction.resolve",
        "params": {"interactionId": "int-1", "decision": "allow-once"},
    }
    response = {"type": "res", "id": request_id, "ok": accepted}
    if not accepted:
        response["ok"] = False
        response["error"] = {"code": "UNAVAILABLE", "message": "rejected"}
    return EngineInteractionResolveExchange.from_frames(
        request=request, response=response
    )


def _claim_service_for(resolution: InteractionResolution) -> MagicMock:
    service = MagicMock()
    service.claim_for_dispatch.return_value = InteractionDispatch(
        session_key="sk1",
        interaction_id="int-1",
        resolution=resolution,
    )
    return service
