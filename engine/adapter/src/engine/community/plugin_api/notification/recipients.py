from __future__ import annotations

import os

from engine.community.plugin_api.cron.models import CronJob


def get_default_user_ids() -> list[str]:
    """Resolve default notification users from env or credentials file."""
    if staff_id := os.getenv("STAFF_ID"):
        return [staff_id]

    try:
        path = os.getenv("CREDENTIALS_PATH", "/home/admin/.credentials")
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CLIENT_ID="):
                    value = line[10:]
                    parts = value.split("_")
                    if len(parts) >= 2 and parts[0] == "staff":
                        return [parts[1]]
    except Exception:
        pass

    return []


def resolve_user_ids(job: CronJob) -> list[str]:
    """Resolve user IDs for a cron notification."""
    if job.notify and job.notify.user_ids:
        return job.notify.user_ids

    default_users = get_default_user_ids()
    if default_users:
        return default_users

    return []
