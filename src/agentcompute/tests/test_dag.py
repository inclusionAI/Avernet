import pytest

from agentcompute.community.core import DAGCycleError, DAGDanglingEdgeError, DAGNode, DAGPlan


def test_serialize_roundtrip():
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="searcher", input={"goal": "g"}))
    plan.add_node(DAGNode(id="b", agent="summarizer"))
    plan.add_edge("a", "b")
    assert DAGPlan.from_json(plan.to_json()).to_dict() == plan.to_dict()


def test_topological_order_respects_edges():
    plan = DAGPlan()
    for nid in ("a", "b", "c"):
        plan.add_node(DAGNode(id=nid, agent="x"))
    plan.add_edge("a", "c")
    plan.add_edge("b", "c")
    order = plan.topological_order()
    assert order.index("c") > order.index("a")
    assert order.index("c") > order.index("b")


def test_rejects_cycle():
    plan = DAGPlan()
    for nid in ("a", "b"):
        plan.add_node(DAGNode(id=nid, agent="x"))
    plan.add_edge("a", "b")
    plan.add_edge("b", "a")
    with pytest.raises(DAGCycleError):
        plan.validate()


def test_rejects_dangling_edge():
    plan = DAGPlan()
    plan.add_node(DAGNode(id="a", agent="x"))
    plan.add_edge("a", "ghost")
    with pytest.raises(DAGDanglingEdgeError):
        plan.validate()
