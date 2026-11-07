"""Serial TC migration through the fixed-new v2 API, never through v1 sync."""

import time
from urllib.parse import quote

from search_core.api.cards import CardRecord
from search_core.catalog.cards import CardSnapshot


class ApiMigrationRepository:
    def __init__(self, client, environment, *, index_timeout=60):
        self.client, self.environment = client, environment
        self.index_timeout = index_timeout
        ready = client.get("/ready")
        ready.raise_for_status()
        if ready.json().get("environment") != environment:
            raise ValueError(
                "TC service environment differs from migration environment"
            )

    @staticmethod
    def _path(worker_id):
        return f"/api/v2/agent-cards/{quote(worker_id, safe='')}"

    def read_card(self, agent_id):
        response = self.client.get(self._path(agent_id))
        deleted = response.status_code == 404
        if deleted:
            if response.json() != {"detail": "Card not found"}:
                response.raise_for_status()
            status = self.client.get(self._path(agent_id) + "/index-status")
            if status.status_code == 404:
                if status.json() != {"detail": "Card not found"}:
                    status.raise_for_status()
                return None
            status.raise_for_status()
            body = status.json()
            if not body["deleted"]:
                raise RuntimeError(
                    "Card changed during migration; retry after stopping writes"
                )
            data, revision, resource_type, config = (
                {},
                body["card_version"],
                "agent",
                None,
            )
        else:
            response.raise_for_status()
            body = response.json()
            data, revision, resource_type = (
                body["card"],
                body["card_version"],
                body["resource_type"],
            )
            result = self.client.get(
                f"/api/v2/workers/{quote(agent_id, safe='')}/config"
            )
            result.raise_for_status()
            config = result.json()["config"]
        return CardRecord(
            CardSnapshot(
                agent_id=agent_id,
                source_version=str(revision),
                source_card=data,
                labels={} if deleted else body.get("labels", {}),
                resource_type=resource_type,
                deleted=deleted,
            ),
            revision,
            0,
            None,
            "",
            config=config,
        )

    def _wait(self, agent_id, revision, *, deleted=False):
        deadline = time.monotonic() + self.index_timeout
        while time.monotonic() < deadline:
            response = self.client.get(self._path(agent_id) + "/index-status")
            response.raise_for_status()
            status = response.json()
            if status["environment"] != self.environment:
                raise ValueError("TC environment changed during migration")
            if status["doc_error"] or status["index_error"]:
                raise RuntimeError("TC index preparation or synchronization failed")
            if (
                status["deleted"] == deleted
                and status["card_version"] >= revision
                and status["indexed_card_version"] >= status["card_version"]
                and (deleted or status["doc_ready"])
            ):
                return
            time.sleep(0.1)
        raise TimeoutError("TC index synchronization timed out")

    def upsert(self, agent_id, fields, resource_type, search_config, *, labels=None):
        response = self.client.put(
            self._path(agent_id),
            json={"card": fields, "resource_type": resource_type, "labels": labels},
        )
        response.raise_for_status()
        self._wait(agent_id, response.json()["card_version"])
        row = self.read_card(agent_id)
        if row is None or row.card.deleted:
            raise RuntimeError("Card disappeared during migration")
        return row

    def save_config(self, agent_id, expected_revision, config):
        response = self.client.put(
            f"/api/v2/workers/{quote(agent_id, safe='')}/config",
            json={"config": config, "version": expected_revision},
        )
        response.raise_for_status()
        return True

    def remove(self, agent_id):
        # Existing absence already satisfies deletion; retain the CSV archive evidence.
        if self.read_card(agent_id) is None:
            return
        response = self.client.delete(self._path(agent_id))
        response.raise_for_status()
        self._wait(agent_id, 0, deleted=True)
