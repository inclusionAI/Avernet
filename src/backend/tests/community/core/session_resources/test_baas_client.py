from __future__ import annotations

import httpx

from agentclaw.community.core.session_resources.baas_client import (
    SessionResourceBaasClient,
)


class _Http:
    def __init__(self) -> None:
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        request = httpx.Request("POST", f"https://baas.example{path}")
        if path.endswith("upload-url"):
            data = {
                "transfer_id": "transfer-1",
                "type": "MULTIPART",
                "upload_session_id": "upload-session-1",
                "part_size": 1048576,
                "part_count": 2,
                "parts": [
                    {"part_number": 1, "upload_url": "https://oss.example/part-1"},
                    {"part_number": 2, "upload_url": "https://oss.example/part-2"},
                ],
            }
        else:
            data = {"transfer_id": "transfer-1", "status": "DONE"}
        return httpx.Response(200, request=request, json={"code": 0, "data": data})


def test_session_upload_grant_preserves_multipart_contract_without_logging_urls():
    http = _Http()
    client = SessionResourceBaasClient(http)

    grant = client.create_session_upload_grant(
        tenant="tenant-1",
        session_id="session/value",
        filename="report.txt",
        file_size=2_000_000,
        operator="owner-1",
    )

    assert http.calls[0][0] == "/api/v1/sessions/tenant-1/session%2Fvalue/files/upload-url"
    assert grant.upload_type == "MULTIPART"
    assert grant.upload_url is None
    assert grant.part_count == 2
    assert grant.parts[0]["part_number"] == 1


def test_session_complete_uses_transfer_as_one_encoded_path_segment():
    http = _Http()
    client = SessionResourceBaasClient(http)

    status = client.complete_session_upload(
        tenant="tenant-1",
        session_id="session/value",
        transfer_id="transfer/value",
    )

    assert status == "DONE"
    assert http.calls[0][0].endswith("upload-url/transfer%2Fvalue/complete")
    assert http.calls[0][1]["json"] is None
