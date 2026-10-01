from __future__ import annotations

import threading

from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
    CorrelationRecord,
    InMemoryCallbackCorrelationRegistry,
)


def _make_reg():
    return InMemoryCallbackCorrelationRegistry()


class TestRegistry:
    def test_register_then_resolve(self):
        reg = _make_reg()
        reg.register(
            source="bcn", workflow_id=7, instance_id=77,
            task_id="t1", node_id="n1", loop_task_id="t1::n1",
            workflow_id_str="w7", instance_id_str="inst77",
        )
        rec = reg.resolve("bcn", "inst77")
        assert rec == CorrelationRecord("t1", "n1", "t1::n1", 7, 77)

    def test_resolve_missing_returns_none(self):
        reg = _make_reg()
        assert reg.resolve("claw_mind", "none") is None

    def test_register_is_idempotent_overwrite(self):
        reg = _make_reg()
        reg.register(source="bcn", workflow_id=7, instance_id=77,
                     task_id="t1", node_id="n1", loop_task_id="t1::n1",
                     workflow_id_str="w7", instance_id_str="inst77")
        reg.register(source="bcn", workflow_id=7, instance_id=77,
                     task_id="t1", node_id="n2", loop_task_id="t1::n2",
                     workflow_id_str="w7", instance_id_str="inst77")
        assert reg.resolve("bcn", "inst77").node_id == "n2"

    def test_keyed_by_source_and_instance(self):
        reg = _make_reg()
        reg.register(source="bcn", workflow_id=7, instance_id=77,
                     task_id="t1", node_id="n1", loop_task_id="t1::n1",
                     workflow_id_str="w7", instance_id_str="inst77")
        reg.register(source="claw_mind", workflow_id=8, instance_id=88,
                     task_id="t2", node_id="n2", loop_task_id="t2::n2",
                     workflow_id_str="w8", instance_id_str="inst77")  # 同 instance_id_str,不同 source
        assert reg.resolve("bcn", "inst77").task_id == "t1"
        assert reg.resolve("claw_mind", "inst77").task_id == "t2"

    def test_concurrent_register_resolve(self):
        reg = _make_reg()

        def worker(i):
            reg.register(source="bcn", workflow_id=i, instance_id=i,
                         task_id=f"t{i}", node_id=f"n{i}", loop_task_id=f"t{i}::n{i}",
                         workflow_id_str=f"w{i}", instance_id_str=f"inst{i}")
            reg.resolve("bcn", f"inst{i}")

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert reg.resolve("bcn", "inst25") is not None

    def test_protocol_runtime_checkable(self):
        assert isinstance(_make_reg(), CallbackCorrelationRegistry)