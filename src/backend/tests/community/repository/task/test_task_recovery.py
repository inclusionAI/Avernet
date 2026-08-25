import asyncio

from agentclaw.community.core.task.task_center.recovery import TaskRecoveryWorker


class _Repo:
    def __init__(self):
        self.claimed = []
        self.released = []

    def list_recoverable(self, *, limit=100):
        return ["t1", "t2"][:limit]

    def acquire_lease(self, task_id, *, instance_id, lease_seconds):
        if task_id == "t2":
            return False
        self.claimed.append((task_id, instance_id, lease_seconds))
        return True

    def load_graph(self, task_id):
        return object()

    def release_lease(self, task_id, *, instance_id):
        self.released.append((task_id, instance_id))
        return True



def test_recovery_worker_claims_resumes_and_releases():
    repo = _Repo()
    resumed = []

    async def resume(task_id):
        resumed.append(task_id)

    worker = TaskRecoveryWorker(repo, resume, instance_id="pod-a", lease_seconds=30)
    recovered = asyncio.run(worker.recover_once())

    assert recovered == ["t1"]
    assert resumed == ["t1"]
    assert repo.released == [("t1", "pod-a")]
