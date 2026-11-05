"""Drive the real Manifest creation/apply task handlers and persist task outcomes."""

from agentclaw.community.api.apply_run_service import ApplyRunServiceProtocol
from agentclaw.community.core.apply_engine.apply_task import APPLY_TASK_TYPE
from agentclaw.community.core.bot_config_manifest.create_job import (
    CREATE_JOB_TASK_TYPE,
    BotCreateWithManifestHandler,
)
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.task_queue.types import (
    DEFAULT_APP,
    Complete,
    Fail,
    Reschedule,
)
from agentclaw.community.utils.env_utils import get_current_env


class _Worker:
    """A worker, reduced to one deployment and driven a turn at a time.

    It claims, dispatches and **applies the outcome to the row**, because that
    last part is what the poll reads: a job that returned ``Fail`` but whose row
    was never transitioned still looks live, and the poll would go on reporting
    `AWAITING_AUTHORIZATION` for a creation that had already been declined.
    Running the handlers without the transitions would have made these cases
    assert against a state the real system never produces.

    Two liberties, both deliberate. Rescheduled tasks come back immediately
    rather than after the job's five-second poll delay — the delay is latency,
    not sequencing, and waiting it out would only make the suite slow. And a
    task type this file does not drive is put back far enough not to return,
    rather than completed, so nothing else in the app is quietly retired.
    """

    _WORKER = "test-worker"

    def __init__(self, world) -> None:
        self._world = world
        self._repo = world.get(TaskQueueRepositoryProtocol)

    def _dispatch(self, task):
        if task.task_type == CREATE_JOB_TASK_TYPE:
            return self._world.get(BotCreateWithManifestHandler).handle(task.payload)
        if task.task_type == APPLY_TASK_TYPE:
            self._world.get(ApplyRunServiceProtocol).run_apply_task(task.payload)
            return Complete()
        return None

    def _settle(self, task, outcome) -> None:
        if outcome is None or isinstance(outcome, Reschedule):
            self._repo.reschedule(
                task_id=task.id,
                worker_id=self._WORKER,
                delay_seconds=0 if outcome is not None else 3600,
            )
        elif isinstance(outcome, Fail):
            self._repo.fail(
                task_id=task.id, worker_id=self._WORKER, error=outcome.error
            )
        else:
            self._repo.complete(task_id=task.id, worker_id=self._WORKER)

    def turn(self) -> list:
        claimed = self._repo.claim_batch(
            worker_id=self._WORKER,
            env=get_current_env(),
            app=DEFAULT_APP,
            limit=10,
            lease_seconds=300,
        )
        settled = []
        for task in claimed:
            outcome = self._dispatch(task)
            self._settle(task, outcome)
            if task.task_type == CREATE_JOB_TASK_TYPE:
                settled.append(outcome)
        return settled

    def run_to_the_end(self, *, turns: int = 8):
        for _ in range(turns):
            for outcome in self.turn():
                if isinstance(outcome, (Complete, Fail)):
                    # Drain whatever this last turn enqueued — the
                    # post-container apply is started by the job's final turn.
                    self.turn()
                    return outcome
        raise AssertionError("the creation never reached a terminal outcome")
