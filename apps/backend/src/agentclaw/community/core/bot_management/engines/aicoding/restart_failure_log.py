"""Coding restart failure diagnostics; never changes restart/retry decisions."""
from __future__ import annotations

import re
import traceback
from pathlib import PurePath

from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

from .restart_state import RestartSuperseded

logger = get_logger()
MARKER = "AICODING_RESTART_FAILED"


def safe_log_field(value) -> str:
    if not isinstance(value, (str, int)):
        return "-"
    # Keep correlation fields single-line and prevent log-field injection.
    return re.sub(r"[^\w.:@/+-]", "_", str(value))[:256] or "-"


def arca_identity(target_id, sandbox_id=None) -> str:
    """Only a captured sandbox or an ARCA provider ID identifies a container.

    Preserve the full routing identity, including any @tenant suffix, in logs.
    A logical BaaS Bot/device ID must never be advertised as an ARCA ID.
    """
    if isinstance(sandbox_id, str) and sandbox_id:
        return safe_log_field(sandbox_id)
    if isinstance(target_id, str) and target_id.startswith("ARCA-SANDBOX-"):
        return safe_log_field(target_id)
    return "-"


def log_restart_failure(
    error: Exception | None = None, *, bot_id, phase, target_id=None,
    sandbox_id=None, operation_id=None, reason=None, elapsed_ms=None,
    helper_status=None, binding_id=None, publish_id=None, owner_id=None,
    physical_targets=(),
) -> None:
    """Emit safe failure evidence, retaining a lower-level physical target.

    Do not print exception messages, source lines, locals, commands or helper
    output: provider exceptions may embed credentials. Chained exception types
    and filename/line/function frames still identify the failure location.
    No provider/database lookup is performed for logging.
    """
    # Losing ownership is a reschedule/no-op, not a failed restart.
    if isinstance(error, RestartSuperseded):
        return
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    details = dict(
        bot_id=bot_id, target_id=target_id,
        arca_id=arca_identity(target_id, sandbox_id), operation_id=operation_id,
        elapsed_ms=elapsed_ms, helper_status=helper_status, owner_id=owner_id,
        env=get_current_env(), physical_targets=tuple(sorted(physical_targets)),
    )
    # A backup exception may cross inventory/task/publish wrappers. Keep the
    # original physical target rather than overwriting it with a logical ID.
    for cause in reversed(chain):
        captured = getattr(cause, "_coding_restart_log_context", None)
        if isinstance(captured, dict):
            for key, value in captured.items():
                if key in details and value is not None and value not in ("-", "", ()):
                    details[key] = value
    if error is not None:
        error.__dict__["_coding_restart_log_context"] = details
    if reason is None:
        reason = getattr(error, "reason", None)
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{1,64}", reason):
            reason = "timeout" if isinstance(error, TimeoutError) else "unexpected_error"
    frames = []
    for cause in chain:
        frames.extend(
            f"{safe_log_field(PurePath(frame.filename).name)}:{frame.lineno}:{safe_log_field(frame.name)}"
            for frame in traceback.extract_tb(cause.__traceback__)[-8:]
        )
    # Inventory-level failures can concern several captured containers. Emit one
    # record per target, without choosing an arbitrary replica or re-querying.
    targets = ([(target, arca_identity(target)) for target in details['physical_targets']]
               if details['arca_id'] == "-" and details['physical_targets']
               else [(details['target_id'], details['arca_id'])])
    for target_id, arca_id in targets:
        logger.error(
            "[%s] event=aicoding_restart_failed status=failed "
            "bot_id=%s arca_id=%s target_id=%s operation_id=%s "
            "phase=%s reason=%s error_type=%s cause_types=%s "
            "elapsed_ms=%s helper_status=%s binding_id=%s publish_id=%s "
            "env=%s owner_id=%s stack=%s",
            MARKER, safe_log_field(details['bot_id']), safe_log_field(arca_id),
            safe_log_field(target_id), safe_log_field(details['operation_id']),
            safe_log_field(phase), safe_log_field(reason), safe_log_field(type(error).__name__) if error is not None else "-",
            ">".join(safe_log_field(type(cause).__name__) for cause in chain) or "-",
            safe_log_field(details['elapsed_ms']), safe_log_field(details['helper_status']),
            safe_log_field(binding_id), safe_log_field(publish_id), safe_log_field(details['env']),
            safe_log_field(details['owner_id']), "|".join(frames) or "-",
        )
