"""Coding-only submission fencing and definitive-rejection classification."""

from __future__ import annotations

from .restart_state import RestartSuperseded, current_restart, journal


class RestartSubmissionRejected(RuntimeError):
    """The provider explicitly rejected the request; do not await a publish."""


def rejection_status(error: Exception) -> int | None:
    """Walk wrappers without persisting transport bodies, URLs or credentials.

    Timeouts, conflict/idempotency responses and server errors are deliberately
    NOT definitive: they may follow an accepted provider mutation.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and status in {400, 401, 403, 404, 422}:
            return status
        error = error.__cause__ or error.__context__
    return None


def find_rejection(error: Exception) -> RestartSubmissionRejected | None:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, RestartSubmissionRejected):
            return error
        error = error.__cause__ or error.__context__
    return None


class AicodingSubmissionMixin:
    def before_restart_submission(self, ctx) -> None:
        execution = current_restart.get()
        if execution is None or execution.payload["provider"] != "baas":
            return
        execution.check_target(ctx, execution.payload["binding_id"])
        if execution.verify_backup is None:
            raise RuntimeError("缺少已验证的备份，禁止提交重启")
        # Configuration preparation can take time. Recheck the actual container
        # and committed generation at the last possible moment before submission.
        execution.verify_backup()
        execution.fence_mutation()

    def on_restart_submission_error(self, ctx, error, *, clear_intent) -> None:
        execution = current_restart.get()
        if execution is None or execution.payload["provider"] != "baas":
            return
        status = rejection_status(error) if execution.fenced else None
        if execution.fenced and status is None:
            return
        bot = execution.state.read()
        record = journal(bot)
        expected_phase = "RESTARTING" if execution.fenced else "BACKING_UP"
        if (
            record.get("operation_id") != execution.operation_id
            or record.get("phase") != expected_phase
            or bot.get("binding_id") != execution.payload["binding_id"]
        ):
            raise RestartSuperseded("Restart submission ownership changed")
        if not execution.fenced:
            # Includes payload construction and the final receipt check. The
            # coding executor's submission fence has not authorized POST yet.
            clear_intent()
            return
        # The old provider poller must not adopt an unrelated future publish.
        # A persistence failure propagates: do not pretend cleanup succeeded.
        clear_intent()
        raise RestartSubmissionRejected(
            f"BaaS 拒绝重启请求（HTTP {status}），本次重启失败；旧容器未执行替换"
        ) from error
