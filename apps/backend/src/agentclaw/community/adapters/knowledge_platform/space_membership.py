"""Gateway-admitted ECB membership snapshots; no custom service credential."""

import json

import httpx
from agentclaw.community.adapters.knowledge_platform.space_knowledge import (
    EcbSpaceKnowledgeConfig,
)
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot


class EcbSpaceMembershipSink:
    def __init__(self, config: EcbSpaceKnowledgeConfig, client: httpx.Client):
        self._config, self._client = config, client

    def is_enabled(self) -> bool:
        if self._config.enabled and (
            not self._config.base_url or not self._config.tc_env
        ):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        return self._config.enabled

    def apply_snapshot(self, snapshot: SpaceMembershipSnapshot) -> None:
        cfg = self._config
        if (
            not cfg.enabled
            or not cfg.base_url
            or not cfg.tc_env
            or snapshot.environment != cfg.tc_env
        ):
            raise SpaceKnowledgeError("SPACE_SYNC_UNAVAILABLE")
        body = snapshot.body
        if type(snapshot.space_id) is not int or snapshot.space_id < 1:
            raise SpaceKnowledgeError("INVALID_INPUT")
        encoded = json.dumps(body, ensure_ascii=False).encode()
        if len(body.get("members", [])) > 2000 or len(encoded) > 1024 * 1024:
            raise SpaceKnowledgeError("INVALID_INPUT")
        url = (
            cfg.base_url
            + f"/api/v1/knowledge/integrations/tc/spaces/{snapshot.space_id}"
        )
        try:
            response = self._client.put(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=cfg.timeout,
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE") from None
        if (
            response.status_code != 200
            or not isinstance(data, dict)
            or data.get("tc_env") != snapshot.environment
            or data.get("tc_space_id") != str(snapshot.space_id)
            or type(data.get("tc_revision")) is not int
            or data["tc_revision"] != snapshot.revision
            or data.get("tc_snapshot_hash") != snapshot.digest()
            or data.get("status") != body["status"]
        ):
            raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
