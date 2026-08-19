from pathlib import Path

_BASE = Path("src/agentclaw/community/adapters/http/openapi_v1/task")
_FILES = ["schemas.py", "translator.py", "auth.py", "router.py", "__init__.py"]
_FORBIDDEN = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]


def test_no_node_name_literals():
    hits = []
    for f in _FILES:
        src = (_BASE / f).read_text()
        hits += [f"{f}:{tok}" for tok in _FORBIDDEN if tok in src]
    assert hits == [], f"task callback 出现写死节点名: {hits}"