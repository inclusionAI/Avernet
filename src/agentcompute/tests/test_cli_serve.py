from agentcompute import main


def test_serve_subcommand_parses(monkeypatch):
    called = {}

    def _mock_serve(args):
        called["config"] = args.config
        called["port"] = args.port
        return 0

    monkeypatch.setattr("agentcompute._cli._serve", _mock_serve)
    assert main(["serve", "--config", "config/application.yaml", "--port", "9000"]) == 0
    assert called["config"] == "config/application.yaml"
    assert called["port"] == 9000


def test_legacy_run_default(monkeypatch):
    called = {}

    def _mock_run_cli(args):
        called["goal"] = args.goal
        return 0

    monkeypatch.setattr("agentcompute._cli._run_cli", _mock_run_cli)
    monkeypatch.setattr(
        "agentcompute._cli._parse_agents",
        lambda raw: [("searcher", "finds")],
    )
    # legacy positional form should be transparently wrapped into "run"
    assert main(["my goal", "-a", "searcher:finds"]) == 0
    assert called["goal"] == "my goal"
