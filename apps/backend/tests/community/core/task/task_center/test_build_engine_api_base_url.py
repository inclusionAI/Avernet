from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def test_build_centralized_adapter_passes_api_base_url():
    graph = TaskGraphService()
    svc = TaskService(graph, task_info_repo=None, api_base_url="http://my-backend:9999")
    assert svc._centralized_adapter._api_base_url == "http://my-backend:9999"
