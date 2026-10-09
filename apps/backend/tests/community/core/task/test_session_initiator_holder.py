from agentclaw.community.core.task.task_discovery.session_initiator import FrontendUrlHolder


def test_frontend_url_holder_normalizes_and_returns_runtime_override():
    FrontendUrlHolder.set("http://frontend.example/ ")
    assert FrontendUrlHolder.get() == "http://frontend.example/ "
    FrontendUrlHolder.set("http://frontend.example///")
    assert FrontendUrlHolder.get() == "http://frontend.example"
