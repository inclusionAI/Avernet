"""架构测试:products/ import 白名单机械执法(spec §3.1,乙案裁决 H)。

白名单 = 协议层模块(l*/g*) + errors + 三处基建端口(仓储协议、
MethodStore、L5 顶层投影)。``service_api``/通用服务等其余模块对产品
永久禁止;违规即红。含"故意违规探针"自证测试有效(先证红再撤探针)。
"""

import ast
from pathlib import Path

# 以测试文件自身锚定(src 树),与 cwd 无关;不存在即响亮失败,杜绝"空转过绿"。
_PRODUCTS_SRC = (
    Path(__file__).resolve().parents[6]
    / "src/agentclaw/community/core/task/context_collab/products"
)
assert _PRODUCTS_SRC.is_dir(), f"products src tree missing: {_PRODUCTS_SRC}"

_CC = "agentclaw.community.core.task.context_collab"
# 层模块 + errors + 三处基建端口(spec §3.1 逐字)。
_ALLOWED_CONTEXT_COLLAB_PARTS = {
    "l2_agent_network",
    "l3_spaces_resources",
    "l4_context_primitives",
    "l5_methods",
    "l6_products",
    "errors",
    "g1_secure_govern",
    "g2_keep_reliable_live",
    "g3_observe_evaluate_evolve",
    "g4_contracts_consistency",
    "persistence.repository",
    "persistence.method_store",
    "services.method_lifecycle_service",
}
def _imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _violating_modules(modules: list[str]) -> list[str]:
    """违规 agentclaw 模块清单。

    裁定面(spec §3.1"全部 import"):任何 ``agentclaw.*`` import 都必须
    落在白名单内——context_collab 之外的模块(如 core.task.forum)一律
    违规;标准库/第三方不在此列(无 agentclaw 前缀)。静态 AST 扫描
    不覆盖 importlib/__import__ 动态导入(documented limitation)。
    """
    _layer_dirs = {
        "l2_agent_network", "l3_spaces_resources", "l4_context_primitives",
        "l5_methods", "l6_products", "g1_secure_govern",
        "g2_keep_reliable_live", "g3_observe_evaluate_evolve",
        "g4_contracts_consistency",
    }
    out = []
    for module in modules:
        if not module.startswith("agentclaw."):
            continue  # 标准库/第三方不裁。
        if not module.startswith(_CC + "."):
            out.append(module)  # agentclaw 但在 context_collab 之外 → 违规。
            continue
        rest = module[len(_CC) + 1 :]
        # products/ 子树内部互引(含 products 自身)允许。
        if rest == "products" or rest.startswith("products."):
            continue
        if rest in _ALLOWED_CONTEXT_COLLAB_PARTS:
            continue
        # 层模块的成员引用(如 l4_context_primitives.collet)允许。
        if rest.split(".")[0] in _layer_dirs:
            continue
        out.append(module)
    return out



def test_probe_blocks_agentclaw_outside_context_collab():
    """执法盲区自证:context_collab 之外的 agentclaw 模块(如 forum)必须违规。"""
    bad = _imported_modules(
        ast.parse(
            "from agentclaw.community.core.task.forum.services.forum_service import ForumService\n"
        )
    )
    assert _violating_modules(bad), (
        "checker under-enforces: agentclaw imports outside context_collab "
        "must be violations"
    )

def test_products_import_whitelist():
    """products/ 下每个文件的所有 agentclaw import 必须落在白名单内。"""
    violations: list[tuple[Path, list[str]]] = []
    for path in sorted(_PRODUCTS_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = _violating_modules(_imported_modules(tree))
        if bad:
            violations.append((path, bad))
    assert not violations, f"products 依赖违约(spec §3.1):\n{violations}"


def test_probe_violation_is_detected():
    """自证:把一段 service_api 违规 import 喂给检查器必须被抓出来。"""
    bad = _imported_modules(
        ast.parse("from agentclaw.community.core.task.context_collab.service_api import ActorContext\n")
    )
    assert _violating_modules(bad) == [
        "agentclaw.community.core.task.context_collab.service_api"
    ]
    good = _imported_modules(
        ast.parse(
            "from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import Collet\n"
        )
    )
    assert _violating_modules(good) == []
