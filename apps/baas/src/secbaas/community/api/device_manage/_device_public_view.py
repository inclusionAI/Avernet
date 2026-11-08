"""Public-view helper for device query endpoints.

Per the privacy contract for ``GET /api/v1/devices/{device_uuid}`` and
``GET /api/v1/bots/{bot_uuid}/devices``, these query endpoints must not
expose ``provider_device_props`` or ``extra_config.deploy_config``; the
former carries provider envs and the latter outbound operation rules.

The masking lives at the router response-construction layer (see
``device_router.get_device_info`` and
``management_router.get_bot_devices_by_uuid``) rather than in the shared
service mapper ``device_record_to_response``, which is reused by 20+
service flows (``devices-by-id``, publish progress, device
create/update/destroy/restart echoes, etc.) that must keep returning the
full contract.
"""

from ._device import DeviceResponse

__all__ = ["to_public_device_response"]


def to_public_device_response(device: DeviceResponse) -> DeviceResponse:
    """Return a copy of ``device`` safe for HTTP public exposure.

    Resets ``provider_device_props`` to ``None`` and, when
    ``extra_config`` is present, also resets its ``deploy_config``
    sub-field to ``None``. All other contract fields are preserved
    bit-for-bit. The returned object is a shallow ``model_copy`` of the
    input; the caller's original ``DeviceResponse`` is left untouched so
    service-layer flows that hold the same record are unaffected.

    Args:
        device: A ``DeviceResponse`` produced by the device service.

    Returns:
        A new ``DeviceResponse`` with the two sensitive fields masked.
    """
    public = device.model_copy()
    public.provider_device_props = None
    if public.extra_config is not None:
        public.extra_config = public.extra_config.model_copy()
        public.extra_config.deploy_config = None
    return public
