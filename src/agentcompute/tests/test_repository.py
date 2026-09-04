from agentcompute.community.core import DAGNode, DAGPlan, NodeStatus, RunRepository
from agentcompute.community.plugins.database import SqliteDatabasePlugin


def _repo(tmp_path) -> RunRepository:
    db = SqliteDatabasePlugin(f"sqlite:///{tmp_path / 'test.db'}")
    repo = RunRepository(db=db)
    repo.init_schema()
    return repo


def test_persist_full_lifecycle(tmp_path):
    repo = _repo(tmp_path)
    request_id = repo.save_request(
        {"goal": "research X", "agents": ["searcher", "summarizer"], "provider": "stub"}
    )
    run_id = repo.start(request_id, "research X", ["searcher", "summarizer"], "stub")

    plan = DAGPlan()
    plan.add_node(DAGNode(id="node-1", agent="searcher", input={"goal": "research X"}))
    plan.add_node(DAGNode(id="node-2", agent="summarizer"))
    plan.add_edge("node-1", "node-2")
    repo.mark_running(run_id)
    repo.save_plan(run_id, plan)

    repo.touch_node(run_id, "node-1", "searcher", "RUNNING", 0.5)
    repo.finish_node(run_id, "node-1", NodeStatus.SUCCEEDED, {"echo": "x"}, None)
    repo.touch_node(run_id, "node-2", "summarizer", "RUNNING", 0.0)
    repo.finish_node(run_id, "node-2", NodeStatus.FAILED, None, "boom")
    repo.finish(run_id, "failed")

    requests = repo.db.execute("SELECT * FROM requests").fetchall()
    assert len(requests) == 1
    assert requests[0]["goal"] == "research X"

    runs = repo.db.execute("SELECT * FROM runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["goal"] == "research X"
    assert runs[0]["request_id"] == request_id

    plans = repo.db.execute("SELECT plan_json FROM plans").fetchall()
    assert len(plans) == 1
    import json

    assert json.loads(plans[0]["plan_json"])["nodes"]["node-1"]["agent"] == "searcher"

    nodes = repo.db.execute("SELECT * FROM node_executions ORDER BY node_id").fetchall()
    assert len(nodes) == 2
    assert nodes[0]["status"] == "SUCCEEDED"
    assert nodes[0]["result"] is not None
    assert nodes[1]["status"] == "FAILED"
    assert nodes[1]["error"] == "boom"
    assert nodes[1]["progress"] == 1.0


def test_touch_node_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    request_id = repo.save_request({"goal": "g", "agents": ["searcher"], "provider": "stub"})
    run_id = repo.start(request_id, "g", ["searcher"], "stub")
    repo.touch_node(run_id, "n", "searcher", "RUNNING", 0.0)
    repo.touch_node(run_id, "n", "searcher", "RUNNING", 0.5)
    rows = repo.db.execute("SELECT progress FROM node_executions WHERE node_id='n'").fetchall()
    assert len(rows) == 1
    assert rows[0]["progress"] == 0.5


def test_fail_planning(tmp_path):
    repo = _repo(tmp_path)
    request_id = repo.save_request({"goal": "g", "agents": ["searcher"], "provider": "stub"})
    run_id = repo.start(request_id, "g", ["searcher"], "stub")
    repo.fail_planning(run_id, "no valid DAG")
    run = repo.db.execute("SELECT status, error FROM runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "failed_planning"
    assert run["error"] == "no valid DAG"
