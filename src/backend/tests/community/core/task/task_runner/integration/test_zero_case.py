from pathlib import Path

# 源 task_runner 目录(与本测试目录同在 backend/ 下,经 __file__ 定位,不依赖 cwd)。
_BASE = (Path(__file__).resolve().parents[6]
         / "src" / "agentclaw" / "community" / "core" / "task" / "task_runner")
_FILES = [
    "client/ports.py", "client/translators.py", "client/open_api_bot_adapter.py",
    "client/bcs_http_adapter.py", "client/bcs_token_provider.py", "client/prompt_formatter.py",
    "client/__init__.py", "modal_executor/task_executor.py",
    "modal_executor/task_executor_result_poller.py", "modal_executor/bbs_modal_executor.py",
]
_FORBIDDEN = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]


def test_no_node_name_literals():
    hits = []
    for f in _FILES:
        src = (_BASE / f).read_text()
        hits += [f"{f}:{tok}" for tok in _FORBIDDEN if tok in src]
    assert hits == [], f"integration 出现写死节点名: {hits}"
