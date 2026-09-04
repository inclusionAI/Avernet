from agentcompute.community.spi import (
    Agent,
    AgentContext,
    AgentSpec,
    LLMProviderPlugin,
    NodeResult,
)


class _RecordedAgent(Agent):
    name = "recorded"
    calls: list[str] = []

    def setup(self) -> None:
        self.calls.append("setup")

    def execute(self, ctx: AgentContext) -> NodeResult:
        self.calls.append("execute")
        ctx.report_progress(0.75)
        return NodeResult(node_id=ctx.node_id, output="done")

    def teardown(self) -> None:
        self.calls.append("teardown")

    def halt(self) -> None:
        self.calls.append("halt")


class _NoopAgent(Agent):
    name = "noop"

    def setup(self) -> None:
        pass

    def execute(self, ctx: AgentContext) -> NodeResult:
        return NodeResult(node_id=ctx.node_id)


def test_agent_spec_fields():
    spec = AgentSpec(name="searcher", role="finds", instructions="be thorough")
    assert spec.name == "searcher"
    assert spec.role == "finds"
    assert spec.instructions == "be thorough"


def test_agent_lifecycle_defaults_are_noops():
    agent = _NoopAgent()
    assert agent.teardown() is None
    assert agent.halt() is None


def test_report_progress_invokes_callback():
    seen = []
    ctx = AgentContext(node_id="n", goal="g", on_progress=seen.append)
    ctx.report_progress(0.5)
    assert seen == [0.5]


def test_report_progress_no_callback_is_safe():
    ctx = AgentContext(node_id="n", goal="g")
    ctx.report_progress(0.5)


def test_llm_provider_plugin_is_abstract():
    try:
        LLMProviderPlugin()
    except TypeError:
        pass
    else:
        raise AssertionError("LLMProviderPlugin should be abstract")
