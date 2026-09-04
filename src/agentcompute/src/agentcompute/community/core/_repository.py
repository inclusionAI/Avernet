"""Persistence of requests, runs, plans, and node executions via the database SPI.

Each DAG node maps to exactly one ``node_executions`` record (PK on
``run_id, node_id``); status/progress transitions update that single row.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..spi._database import DatabasePlugin
from ._dag import DAGPlan, NodeStatus

__all__ = ["RunRepository"]

RUN_STATUS = {
    "planning": "planning",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "failed_planning": "failed_planning",
}


@dataclass
class RunRepository:
    db: DatabasePlugin

    def init_schema(self) -> None:
        self.db.connect()

    def save_request(self, payload: dict[str, Any]) -> str:
        request_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO requests (id, goal, agents, provider, payload) VALUES (?, ?, ?, ?, ?)",
            (
                request_id,
                payload.get("goal", ""),
                json.dumps(payload.get("agents", [])),
                payload.get("provider", ""),
                json.dumps(payload),
            ),
        )
        return request_id

    def start(self, request_id: str, goal: str, agents: list[str], provider: str) -> str:
        run_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO runs (id, request_id, goal, agents, provider, status) "
            "VALUES (?, ?, ?, ?, ?, 'planning')",
            (run_id, request_id, goal, json.dumps(agents), provider),
        )
        return run_id

    def mark_running(self, run_id: str) -> None:
        self.db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))

    def save_plan(self, run_id: str, plan: DAGPlan) -> None:
        self.db.execute(
            "INSERT INTO plans (run_id, plan_json) VALUES (?, ?)",
            (run_id, plan.to_json()),
        )

    def fail_planning(self, run_id: str, error: str) -> None:
        self.db.execute(
            "UPDATE runs SET status='failed_planning', error=?, finished_at=datetime('now') "
            "WHERE id=?",
            (error, run_id),
        )

    def touch_node(
        self, run_id: str, node_id: str, agent: str, status: str, progress: float
    ) -> None:
        existing = self.db.execute(
            "SELECT 1 FROM node_executions WHERE run_id=? AND node_id=?",
            (run_id, node_id),
        )
        if existing.fetchone() is None:
            self.db.execute(
                "INSERT INTO node_executions (run_id, node_id, agent, status, progress, started_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (run_id, node_id, agent, status, progress),
            )
        else:
            self.db.execute(
                "UPDATE node_executions SET status=?, progress=? WHERE run_id=? AND node_id=?",
                (status, progress, run_id, node_id),
            )

    def finish_node(
        self,
        run_id: str,
        node_id: str,
        status: NodeStatus,
        result: Any,
        error: str | None,
    ) -> None:
        self.db.execute(
            "UPDATE node_executions SET status=?, result=?, error=?, progress=1.0, "
            "finished_at=datetime('now') WHERE run_id=? AND node_id=?",
            (
                status.value,
                json.dumps(result) if result is not None else None,
                error,
                run_id,
                node_id,
            ),
        )

    def finish(self, run_id: str, status: str, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE runs SET status=?, error=?, finished_at=datetime('now') WHERE id=?",
            (status, error, run_id),
        )
