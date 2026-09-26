"""All-auto approval (#197): every BaaS mutation payload requests server-side
auto-approval, so no client-side approve call is needed.

Pins the two payloads that previously omitted the flag (teclaw create/update via
_build_teclaw_payload, and destroy_bot) plus the ARCA create default.
"""
from agentclaw.community.core.service_bot.services.baas_service import BaasService


class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"code": 0, "data": {"bot_uuid": "b", "publish_id": 9}}


class _Http:
    def __init__(self):
        self.captured = None

    def post(self, path, params=None, json=None, timeout=None):
        self.captured = json
        return _Resp()


def _svc():
    s = object.__new__(BaasService)
    s._default_ttl_minutes = 60
    s._tenant = "t"
    return s


def test_teclaw_payload_auto_approves():
    svc = _svc()
    payload = svc._build_teclaw_payload(
        {"entity_id": "e", "entity_type": "staff", "bot_name": "n"},
        "owner",
        "req-1",
        {"artifact": True},
        template_uuid="tmpl",
        device_count=1,
        ttl_in_minutes=None,
    )
    assert payload["config"]["auto_approve_publish"] is True


def test_destroy_payload_auto_approves():
    svc = _svc()
    svc._http = _Http()
    svc.destroy_bot(bot_uuid="b", operator="op", request_id="req-2")
    assert svc._http.captured["auto_approve_publish"] is True
