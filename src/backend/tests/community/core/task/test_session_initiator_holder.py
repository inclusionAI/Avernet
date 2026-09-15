from agentclaw.community.core.task.task_discovery.frontend_url import FrontendUrlHolder


def test_frontend_url_holder():
    FrontendUrlHolder.set("http://frontend.example/ ")
    assert FrontendUrlHolder.get() == "http://frontend.example/ "
    FrontendUrlHolder.set("http://frontend.example///")
    assert FrontendUrlHolder.get() == "http://frontend.example"