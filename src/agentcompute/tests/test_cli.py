import json

from agentcompute import main, run
from agentcompute._cli import _parse_agents
from agentcompute.community.spi import AgentSpec


def test_parse_agents_from_pairs():
    specs = _parse_agents("searcher:Searches web,summarizer:Summarizes")
    assert [s.name for s in specs] == ["searcher", "summarizer"]
    assert specs[0].role == "Searches web"


def test_parse_agents_pair_without_role_defaults_to_name():
    specs = _parse_agents("programmer")
    assert specs[0].name == "programmer"
    assert specs[0].role == "programmer"


def test_parse_agents_from_json_file(tmp_path):
    agent_file = tmp_path / "agents.json"
    agent_file.write_text(
        json.dumps([{"name": "searcher", "role": "finds"}, {"name": "writer", "role": "writes"}]),
        encoding="utf-8",
    )
    specs = _parse_agents(f"@{agent_file}")
    assert [s.name for s in specs] == ["searcher", "writer"]


def test_run_end_to_end_writes_artifacts(tmp_path):
    plan_path = tmp_path / "plan.json"
    viz_path = tmp_path / "dag.html"
    summary = run(
        "summarize X",
        [AgentSpec(name="searcher", role="finds"), AgentSpec(name="summarizer", role="summarizes")],
        plan_path=plan_path,
        viz_path=viz_path,
        provider="stub",
        env_file=None,
    )
    assert summary["succeeded"] is True
    assert plan_path.exists()
    assert viz_path.exists()
    assert len(summary["node_statuses"]) == 2


def test_main_returns_zero_on_success(tmp_path, capsys):
    viz = tmp_path / "dag.html"
    rc = main(
        [
            "summarize X",
            "-a",
            "searcher:finds,summarizer:summarizes",
            "--provider",
            "stub",
            "--viz",
            str(viz),
            "--env-file",
            "-",
        ]
    )
    assert rc == 0
    assert viz.exists()
