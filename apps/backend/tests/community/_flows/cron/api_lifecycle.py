"""cron route-A adapter CRUD flow definitions for singlebox."""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep


CRON_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-cron-local-adapter-crud",
        covers=["cron"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/cron",
                body={
                    "bot_id": "{bot_id}",
                    "name": "singlebox-cron",
                    "schedule": "0 * * * *",
                    "command": "echo singlebox",
                },
                expect={"success": True, "data": {"name": "singlebox-cron"}},
                extract={"cron_task_id": "data.id"},
            ),
            FlowStep(
                method="GET",
                path="/api/cron",
                query={"bot_id": "{bot_id}"},
                expect={"success": True, "data": [{"name": "singlebox-cron"}]},
            ),
            FlowStep(
                method="PUT",
                path="/api/cron/{cron_task_id}",
                query={"bot_id": "{bot_id}"},
                body={
                    "name": "singlebox-cron-updated",
                    "schedule": "*/15 * * * *",
                    "command": "echo singlebox updated",
                    "enabled": False,
                },
                expect={
                    "success": True,
                    "data": {"name": "singlebox-cron-updated", "enabled": False},
                },
            ),
            FlowStep(
                method="GET",
                path="/api/cron/{cron_task_id}",
                query={"bot_id": "{bot_id}"},
                expect={
                    "success": True,
                    "data": {"name": "singlebox-cron-updated", "enabled": False},
                },
            ),
            FlowStep(
                method="GET",
                path="/api/cron/status",
                query={"bot_id": "{bot_id}"},
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/cron/{cron_task_id}/run",
                query={"bot_id": "{bot_id}", "force": "true"},
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/cron/{cron_task_id}/runs",
                query={"bot_id": "{bot_id}"},
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/cron/running",
                query={"bot_id": "{bot_id}"},
                expect={"success": True},
            ),
            FlowStep(
                method="DELETE",
                path="/api/cron/{cron_task_id}",
                query={"bot_id": "{bot_id}"},
                expect={"success": True},
            ),
        ],
    )
]
