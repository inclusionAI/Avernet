"""Exercise risk HTTP with real input, persistence, queue and a security double."""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from injector import InstanceProvider

from agentclaw.community.core.bot_public_risk.lifecycle import RiskTaskLifecycle
from agentclaw.community.core.bot_public_risk.tasks import SecurityProvider, SecurityReport, SecuritySubmission
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from tests.community._flows.bot_public_risk.api_lifecycle import RISK_FLOWS
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework.flow_runner import run_flow
from tests.community.framework.flow_runner_live import run_flow_live


class SecurityDouble:
    def __init__(self):
        self.submit_count = 0
        self.reports = []

    def submit_agent_scan(self, agent_info):
        assert agent_info["agentCode"] == "risk-flow-agent"
        self.submit_count += 1
        return SecuritySubmission("risk-flow-check", SecurityReport("DETECTING", "https://example.test/risk-flow-check"))

    def get_agent_check_report(self, check_id):
        assert check_id == "risk-flow-check"
        self.reports.append(check_id)
        return SecurityReport(status="PASS", valid_pass=True)


@pytest.mark.e2e
@pytest.mark.parametrize("executor", ["in_process", "live_http"])
def test_risk_flow(app_with_testing_modules, world, monkeypatch, executor):
    security = SecurityDouble()
    world.injector.binder.bind(SecurityProvider, to=InstanceProvider(security))
    make_staff_user(world, user_id="risk-owner")
    make_bot(world, bot_id="risk-flow", owner_id="risk-owner", bot_type="personal", active_engine="openclaw")
    with world.get(DatabasePlugin).orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="risk-flow").one()
        bot.ext = '{"passport":{"agent_code":"risk-flow-agent"}}'
    asyncio.run(world.get(RiskTaskLifecycle).bootstrap())
    worker = world.get(TaskWorker)

    def run_worker():
        async def loop():
            for _ in range(100):
                if await worker.run_once():
                    return
                await asyncio.sleep(0.1)
        asyncio.run(loop())

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()
    server = None
    server_thread = None
    try:
        if executor == "in_process":
            ctx = run_flow(RISK_FLOWS[0], app_with_testing_modules, world)
        else:
            monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app_with_testing_modules, log_level="error", lifespan="off"))
            server_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            server_thread.start()
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            ctx = run_flow_live(RISK_FLOWS[0], base_url=f"http://127.0.0.1:{port}")
        assert ctx["risk_id"] == ctx["replayed_id"] == ctx["current_id"] == ctx["read_id"]
        assert security.submit_count == 1
        assert security.reports == ["risk-flow-check"]
    finally:
        if server is not None:
            server.should_exit = True
            server_thread.join(10)
            assert not server_thread.is_alive()
        thread.join(15)
        assert not thread.is_alive()
